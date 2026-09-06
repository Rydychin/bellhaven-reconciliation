from approval_safety import require_current_finding
import json

import streamlit as st
from bs4 import BeautifulSoup

from crm import fetch_all_accounts
from decision_store import connect, list_decisions
from inspect_sources import BASE_URL, get, session
from matcher import billing_route, candidate_for, money, normalize
from scraper import parse_community


st.set_page_config(page_title="Bellhaven CHOW", layout="wide")
st.title("Approve Billing-Safe Ownership Changes")
st.warning(
    "Approval creates a new Bellhaven account and links the old account. "
    "The old parent, billing history, status, and other fields are preserved."
)


def initialize():
    with connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS chow_executions (
                item_id TEXT NOT NULL,
                version TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                new_account_id TEXT,
                PRIMARY KEY (item_id, version)
            )
        """)


def read_account(account_id):
    value = get(f"/api/v1/accounts/{account_id}").json()

    if isinstance(value, dict) and isinstance(value.get("data"), dict):
        value = value["data"]

    if not isinstance(value, dict) or value.get("account_id") != account_id:
        raise ValueError("Unexpected account response.")

    return value


def execution_for(decision):
    with connect() as db:
        row = db.execute(
            """
            SELECT * FROM chow_executions
            WHERE item_id = ? AND version = ?
            """,
            (decision["item_id"], decision["version"]),
        ).fetchone()

    return dict(row) if row else None


def check_old(original, live, new_id=None):
    allowed = {"updated_at", "chow_current_account"}

    changed = [
        field
        for field, value in original.items()
        if field not in allowed and live.get(field) != value
    ]

    if changed:
        raise ValueError(
            "Old account changed since review: " + ", ".join(changed)
        )

    link = live.get("chow_current_account") or ""

    if link and link != new_id:
        raise ValueError("Old account already links to another account.")


def check_new(account, payload):
    changed = [
        field
        for field, value in payload.items()
        if account.get(field) != value
    ]

    if changed:
        raise ValueError(
            "New account differs from approved fields: " + ", ".join(changed)
        )

    if (
        money(account.get("lifetime_revenue")) != 0
        or money(account.get("outstanding_ar")) != 0
        or account.get("chow_current_account")
        or account.get("duplicate_of_account")
    ):
        raise ValueError("Unexpected billing or relationship fields on new account.")


def check_website(location):
    response = get(location["source_url"])
    current = parse_community(
        BeautifulSoup(response.text, "html.parser"),
        location["source_url"],
    )

    for field in [
        "name", "street", "city", "state", "zip",
        "care_offerings", "phone",
    ]:
        if current.get(field) != location.get(field):
            raise ValueError(
                f"Website {field} changed. Refresh and review the evidence."
            )


def preflight(plan):
    accounts = fetch_all_accounts()
    original = plan["original"]
    payload = plan["new_fields"]

    parents = [
        account for account in accounts
        if account["account_id"] == payload["parent_id"]
    ]

    if (
        len(parents) != 1
        or normalize(parents[0]["name"])
        != normalize("Bellhaven Senior Living (Parent Account)")
        or parents[0].get("parent_id")
        or parents[0].get("status") != "Active"
    ):
        raise ValueError("Bellhaven parent could not be verified.")

    marked = [
        account for account in accounts
        if plan["marker"] in account.get("note", "")
    ]

    if len(marked) > 1:
        raise ValueError("Multiple accounts have this execution reference.")

    new_account = marked[0] if marked else None

    permitted_ids = {original["account_id"]}
    if new_account:
        permitted_ids.add(new_account["account_id"])

    other_matches = [
        account for account in accounts
        if account["account_id"] not in permitted_ids
        and account.get("billing_street")
        and candidate_for(plan["location"], account) is not None
    ]

    if other_matches:
        raise ValueError(
            "Other possible facility accounts exist. Review them first: "
            + ", ".join(account["account_id"] for account in other_matches)
        )

    old_live = read_account(original["account_id"])
    check_old(
        original,
        old_live,
        new_account["account_id"] if new_account else None,
    )

    if not billing_route(
        old_live, payload["parent_id"]
    ).startswith("CHOW required:"):
        raise ValueError("Live billing values no longer require this CHOW plan.")

    if new_account:
        check_new(new_account, payload)

    return new_account


def execute(decision, plan):
    if execution_for(decision) is None:
        require_current_finding(decision)
    check_website(plan["location"])
    existing = preflight(plan)

    # Commit approval and the creation-attempt record BEFORE sending POST.
    with connect() as db:
        journal = db.execute(
            """
            SELECT * FROM chow_executions
            WHERE item_id = ? AND version = ?
            """,
            (decision["item_id"], decision["version"]),
        ).fetchone()

        first_attempt = journal is None

        if first_attempt:
            if existing:
                raise ValueError(
                    "A referenced account exists without a local journal. "
                    "Inspect it before continuing."
                )

            claimed = db.execute(
                """
                UPDATE decisions SET status = 'chow_pending'
                WHERE item_id = ? AND version = ? AND status = 'prepared'
                """,
                (decision["item_id"], decision["version"]),
            )

            if claimed.rowcount != 1:
                raise ValueError("Proposal is no longer prepared.")

            db.execute(
                """
                INSERT INTO chow_executions
                (item_id, version, plan_json)
                VALUES (?, ?, ?)
                """,
                (
                    decision["item_id"],
                    decision["version"],
                    json.dumps(plan),
                ),
            )
        else:
            if json.loads(journal["plan_json"]) != plan:
                raise ValueError("The saved execution plan differs.")

    if first_attempt:
        response = session.post(
            f"{BASE_URL}/api/v1/accounts",
            json=plan["new_fields"],
            timeout=30,
            allow_redirects=False,
        )

        if not 200 <= response.status_code < 300:
            raise ValueError(
                f"Creation returned HTTP {response.status_code}: "
                f"{response.text[:400]}"
            )

    # On recovery, this searches for the previous creation; it never POSTs again.
    new_account = preflight(plan)

    if new_account is None:
        raise ValueError(
            "Creation was attempted but no referenced account was found. "
            "Do not reset the journal or create manually. Investigate first."
        )

    new_id = new_account["account_id"]

    with connect() as db:
        db.execute(
            """
            UPDATE chow_executions SET new_account_id = ?
            WHERE item_id = ? AND version = ?
            """,
            (new_id, decision["item_id"], decision["version"]),
        )

    old_id = plan["original"]["account_id"]
    old_live = read_account(old_id)
    check_old(plan["original"], old_live, new_id)

    if old_live.get("chow_current_account") != new_id:
        response = session.patch(
            f"{BASE_URL}/api/v1/accounts/{old_id}",
            json={"chow_current_account": new_id},
            timeout=30,
            allow_redirects=False,
        )

        if not 200 <= response.status_code < 300:
            raise ValueError(
                f"Link update returned HTTP {response.status_code}: "
                f"{response.text[:400]}"
            )

    verified_old = read_account(old_id)
    verified_new = read_account(new_id)

    check_old(plan["original"], verified_old, new_id)
    check_new(verified_new, plan["new_fields"])

    if verified_old.get("chow_current_account") != new_id:
        raise ValueError("The old account's CHOW link was not retained.")

    with connect() as db:
        db.execute(
            """
            UPDATE decisions SET status = 'applied'
            WHERE item_id = ? AND version = ?
            """,
            (decision["item_id"], decision["version"]),
        )

    return verified_old, verified_new


initialize()
decisions = list_decisions()

if any(
    item["status"] in {"creating", "applying", "needs_check", "duplicate_pending"}
    for item in decisions
):
    st.error("Resolve the earlier uncertain write before beginning CHOW.")
    st.stop()

choices = []

for decision in decisions:
    if decision["status"] not in {"prepared", "chow_pending"}:
        continue

    proposal = json.loads(decision["proposal_json"])
    operations = proposal.get("operations", [])

    if len(operations) == 1 and operations[0]["type"] == "chow":
        choices.append(decision)

# Finish interrupted CHOW work before starting another facility.
pending = [item for item in choices if item["status"] == "chow_pending"]
if pending:
    choices = pending

if not choices:
    st.info("No prepared CHOW proposals remain.")
    st.stop()

index = st.selectbox(
    "Facility",
    range(len(choices)),
    format_func=lambda index: json.loads(
        choices[index]["finding_json"]
    )["location"]["name"],
)

decision = choices[index]
finding = json.loads(decision["finding_json"])
location = finding["location"]
proposal = json.loads(decision["proposal_json"])
operation = proposal["operations"][0]
journal = execution_for(decision)

if journal:
    plan = json.loads(journal["plan_json"])
else:
    original = next(
        candidate["account"]
        for candidate in finding["strong_candidates"]
        if candidate["account"]["account_id"] == operation["old_account_id"]
    )

    if original.get("chow_current_account"):
        st.error("The reviewed account already has a CHOW link.")
        st.stop()

    from care_mapping import choose_care_type
    fields = dict(operation["create_fields"])
    fields["care_type"] = choose_care_type(
        location["care_offerings"], decision["item_id"] + decision["version"]
    )

    marker = f"[chow:{decision['item_id']}:{decision['version']}]"
    fields["note"] = (
        f"{marker}\n"
        f"New current account for old account {original['account_id']}.\n"
        f"Source: {location['source_url']}\n"
        f"Website offerings: {', '.join(location['care_offerings'])}.\n"
        f"Reviewed by {decision['reviewer']}: {decision['reason']}"
    )

    plan = {
        "original": original,
        "location": location,
        "new_fields": fields,
        "marker": marker,
    }

st.link_button("Open facility source", location["source_url"])
st.write("Review reason:", decision["reason"])

left, right = st.columns(2)

with left:
    st.subheader("Old account to preserve")
    st.json(plan["original"])

with right:
    st.subheader("New account to create")
    st.json(plan["new_fields"])

st.info(
    "The only business field updated on the old account will be "
    "chow_current_account. The API may also update its timestamp."
)

confirmed = st.checkbox(
    "I approve this new account and the CHOW link on the old account.",
    key=decision["item_id"],
)

label = (
    "Resume approved CHOW"
    if journal
    else "Approve and execute CHOW"
)

if st.button(label, disabled=not confirmed, type="primary"):
    try:
        old, new = execute(decision, plan)
    except Exception as error:
        st.error(str(error))
        st.warning(
            "Stop here and send the error. Do not reset the journal. "
            "Recovery uses the recorded creation reference."
        )
    else:
        st.success("CHOW completed and both accounts verified.")
        st.write("Preserved old account:", old["account_id"])
        st.write("New Bellhaven account:", new["account_id"])
        st.write("Old account links to:", old["chow_current_account"])
        st.json({"old_account": old, "new_account": new})