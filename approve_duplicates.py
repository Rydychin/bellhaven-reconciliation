from approval_safety import require_current_finding
import json

import streamlit as st
from bs4 import BeautifulSoup

from crm import fetch_all_accounts
from decision_store import connect, list_decisions
from inspect_sources import BASE_URL, get, session
from matcher import candidate_for, money, normalize
from scraper import parse_community


st.set_page_config(page_title="Bellhaven Duplicate Review", layout="wide")
st.title("Approve Duplicate Corrections")
st.warning(
    "Approval updates the surviving account where needed, then marks "
    "the losing copies Inactive and links them to the survivor."
)


def read_account(account_id):
    value = get(f"/api/v1/accounts/{account_id}").json()

    if isinstance(value, dict) and isinstance(value.get("data"), dict):
        value = value["data"]

    if not isinstance(value, dict) or value.get("account_id") != account_id:
        raise ValueError("Unexpected account response.")

    return value


def build_plan(decision):
    finding = json.loads(decision["finding_json"])
    proposal = json.loads(decision["proposal_json"])
    operations = proposal["operations"]

    originals = {
        candidate["account"]["account_id"]: candidate["account"]
        for candidate in finding["strong_candidates"]
    }

    if not operations or any(
        operation["type"] != "update" for operation in operations
    ):
        raise ValueError("Expected account-update operations only.")

    targets = [
        operation["account_id"] for operation in operations
    ]

    if len(targets) != len(set(targets)):
        raise ValueError("An account has multiple operations.")

    survivors = {
        operation["fields"]["duplicate_of_account"]
        for operation in operations
        if "duplicate_of_account" in operation["fields"]
    }

    if len(survivors) != 1:
        raise ValueError("Expected exactly one surviving account.")

    survivor_id = next(iter(survivors))

    if survivor_id not in originals:
        raise ValueError("The survivor was not in the reviewed group.")

    patches = {}

    for operation in operations:
        account_id = operation["account_id"]
        fields = operation["fields"]

        if account_id not in originals:
            raise ValueError("An operation targets an unreviewed account.")

        if account_id == survivor_id:
            if not fields or not set(fields).issubset(
                {"name", "parent_id", "billing_zip"}
            ):
                raise ValueError("Unsupported survivor changes.")
        else:
            if (
                set(fields)
                != {"status", "duplicate_of_account", "note"}
                or fields["status"] != "Inactive"
                or fields["duplicate_of_account"] != survivor_id
            ):
                raise ValueError("Invalid losing-account operation.")

        patches[account_id] = fields

    losers = set(originals) - {survivor_id}

    if set(patches) - {survivor_id} != losers:
        raise ValueError("Every losing account must have an operation.")

    final_survivor = {
        **originals[survivor_id],
        **patches.get(survivor_id, {}),
    }

    if final_survivor["name"] != finding["location"]["name"]:
        raise ValueError("Survivor name does not match the reviewed website.")

    if (
        final_survivor.get("status") != "Active"
        or final_survivor.get("duplicate_of_account")
        or final_survivor.get("chow_current_account")
    ):
        raise ValueError("Survivor has an unexpected status or link.")

    return {
        "location": finding["location"],
        "originals": originals,
        "patches": patches,
        "survivor_id": survivor_id,
        "parent_id": final_survivor["parent_id"],
        "order": [survivor_id] + sorted(losers),
    }


def matches_state(live, original, fields):
    expected = {**original, **fields}

    ignored = {"updated_at"}

    # The API derives parent_name when parent_id changes.
    if "parent_id" in fields:
        ignored.add("parent_name")

    return all(
        live.get(field) == value
        for field, value in expected.items()
        if field not in ignored
    )


def check_account(plan, account_id, live, allow_completed):
    original = plan["originals"][account_id]
    fields = plan["patches"].get(account_id, {})

    # This workflow is deliberately limited to these zero-balance groups.
    if (
        money(live.get("lifetime_revenue")) != 0
        or money(live.get("outstanding_ar")) != 0
    ):
        raise ValueError(
            f"{account_id} has nonzero or unknown billing values. "
            "Stop for a separate billing review."
        )

    before = matches_state(live, original, {})

    after = (
        allow_completed
        and matches_state(live, original, fields)
    )

    if not before and not after:
        raise ValueError(
            f"{account_id} changed outside the approved plan. "
            "Refresh and investigate before continuing."
        )


def check_website(location):
    response = get(location["source_url"])
    current = parse_community(
        BeautifulSoup(response.text, "html.parser"),
        location["source_url"],
    )

    for field in ["name", "street", "city", "state", "zip", "care_offerings"]:
        if current.get(field) != location.get(field):
            raise ValueError(
                f"Website {field} changed since review. Review again."
            )


def preflight(plan, allow_completed):
    check_website(plan["location"])

    accounts = fetch_all_accounts()
    by_id = {account["account_id"]: account for account in accounts}

    parent = by_id.get(plan["parent_id"])

    if (
        parent is None
        or normalize(parent["name"])
        != normalize("Bellhaven Senior Living (Parent Account)")
        or parent.get("parent_id")
        or parent.get("status") != "Active"
    ):
        raise ValueError("The Bellhaven parent could not be verified.")

    for account_id in plan["originals"]:
        if account_id not in by_id:
            raise ValueError("A reviewed account is missing.")

        check_account(
            plan, account_id, by_id[account_id], allow_completed
        )

    # Stop if another unreviewed current account now matches the facility.
    extras = [
        account["account_id"]
        for account in accounts
        if account["account_id"] not in plan["originals"]
        and account.get("billing_street")
        and not account.get("duplicate_of_account")
        and not account.get("chow_current_account")
        and candidate_for(plan["location"], account) is not None
    ]

    if extras:
        raise ValueError(
            "Additional possible matches require review: " + ", ".join(extras)
        )

    return by_id


def execute(decision, plan):
    resuming = decision["status"] == "duplicate_pending"
    if not resuming:
        require_current_finding(decision)
    preflight(plan, allow_completed=resuming)

    if not resuming:
        # Record approval before the first CRM write.
        with connect() as db:
            claimed = db.execute(
                """
                UPDATE decisions SET status = 'duplicate_pending'
                WHERE item_id = ? AND version = ? AND status = 'prepared'
                """,
                (decision["item_id"], decision["version"]),
            )

            if claimed.rowcount != 1:
                raise ValueError("Proposal is no longer prepared.")

    # Survivor always comes first.
    for account_id in plan["order"]:
        fields = plan["patches"].get(account_id, {})
        live = read_account(account_id)
        check_account(plan, account_id, live, allow_completed=True)

        if not fields:
            continue

        # A previous attempt may already have completed this exact update.
        if matches_state(
            live, plan["originals"][account_id], fields
        ):
            continue

        response = session.patch(
            f"{BASE_URL}/api/v1/accounts/{account_id}",
            json=fields,
            timeout=30,
            allow_redirects=False,
        )

        if not 200 <= response.status_code < 300:
            raise ValueError(
                f"Update returned HTTP {response.status_code}: "
                f"{response.text[:400]}"
            )

        verified = read_account(account_id)

        if not matches_state(
            verified, plan["originals"][account_id], fields
        ):
            raise ValueError(
                f"Could not verify the exact update for {account_id}."
            )

    verified_group = {}

    for account_id in plan["order"]:
        live = read_account(account_id)
        fields = plan["patches"].get(account_id, {})

        if not matches_state(
            live, plan["originals"][account_id], fields
        ):
            raise ValueError("Final group verification failed.")

        verified_group[account_id] = live

    with connect() as db:
        db.execute(
            """
            UPDATE decisions SET status = 'applied'
            WHERE item_id = ? AND version = ?
              AND status = 'duplicate_pending'
            """,
            (decision["item_id"], decision["version"]),
        )

    return verified_group


decisions = list_decisions()

if any(
    item["status"] in {
        "creating", "applying", "needs_check", "chow_pending"
    }
    for item in decisions
):
    st.error("Finish or verify the earlier operation first.")
    st.stop()

choices = []

for decision in decisions:
    if decision["status"] not in {"prepared", "duplicate_pending"}:
        continue

    finding = json.loads(decision["finding_json"])

    if finding["classification"] == "duplicate_review":
        choices.append(decision)

# Finish a partial group before starting another group.
pending = [
    item for item in choices
    if item["status"] == "duplicate_pending"
]

if pending:
    choices = pending

if not choices:
    st.info("No prepared duplicate groups remain.")
    st.stop()

index = st.selectbox(
    "Duplicate group",
    range(len(choices)),
    format_func=lambda index: json.loads(
        choices[index]["finding_json"]
    )["location"]["name"],
)

decision = choices[index]

try:
    plan = build_plan(decision)
    live_accounts = preflight(
        plan,
        allow_completed=decision["status"] == "duplicate_pending",
    )
except Exception as error:
    st.error(str(error))
    st.stop()

st.write("Review reason:", decision["reason"])
st.link_button("Open source page", plan["location"]["source_url"])
st.write("Surviving account:", plan["survivor_id"])

for account_id in plan["order"]:
    role = (
        "SURVIVOR"
        if account_id == plan["survivor_id"]
        else "LOSING COPY"
    )

    with st.expander(
        f"{role}: {live_accounts[account_id]['name']} · {account_id}",
        expanded=True,
    ):
        st.write("Live account:")
        st.json(live_accounts[account_id])
        st.write("Approved field changes:")
        st.json(plan["patches"].get(account_id, {}))

confirmed = st.checkbox(
    "I approve the survivor and all displayed changes to this group.",
    key=decision["item_id"],
)

label = (
    "Resume approved group"
    if decision["status"] == "duplicate_pending"
    else "Approve and apply group"
)

if st.button(label, disabled=not confirmed, type="primary"):
    try:
        results = execute(decision, plan)
    except Exception as error:
        st.error(str(error))
        st.warning(
            "Stop and send the error. Keep the database intact. "
            "A partial group remains recorded for recovery."
        )
    else:
        st.success("Duplicate group applied and verified.")
        st.write("Survivor:", plan["survivor_id"])

        for account_id, account in results.items():
            if account_id != plan["survivor_id"]:
                st.write(
                    account_id,
                    "→",
                    account["duplicate_of_account"],
                    "·",
                    account["status"],
                )