from approval_safety import require_current_finding
import json

import streamlit as st

from crm import fetch_all_accounts
from decision_store import connect, list_decisions
from inspect_sources import BASE_URL, get, session
from matcher import candidate_for, money, normalize
from scraper import parse_community
from bs4 import BeautifulSoup


st.set_page_config(
    page_title="Create Bellhaven Accounts",
    page_icon="🏡",
    layout="wide",
)

st.title("Approve New Facility Accounts")
st.warning(
    "Clicking Approve and create will create one account in the CRM."
)

CARE_TYPES = {
    "Assisted Living": "Assisted Living",
    "Memory Support": "Memory Care",
    "Short-Term Rehabilitation & Nursing": "Skilled Nursing",
}


def set_status(decision, status):
    with connect() as connection:
        connection.execute(
            """
            UPDATE decisions
            SET status = ?
            WHERE item_id = ? AND version = ?
            """,
            (status, decision["item_id"], decision["version"]),
        )


def verify_website(location):
    response = get(location["source_url"])

    current = parse_community(
        BeautifulSoup(response.text, "html.parser"),
        location["source_url"],
    )

    checked_fields = [
        "name",
        "street",
        "city",
        "state",
        "zip",
        "care_offerings",
        "phone",
    ]

    differences = [
        field
        for field in checked_fields
        if current.get(field) != location.get(field)
    ]

    if differences:
        raise ValueError(
            "The website changed since review: "
            + ", ".join(differences)
            + ". Refresh the pipeline and review again."
        )


def verify_no_match(location, accounts, marker):
    marked = [
        account
        for account in accounts
        if marker in account.get("note", "")
    ]

    if marked:
        raise ValueError(
            "An account already contains this creation reference. "
            "Do not create another account; verify the existing record."
        )

    candidates = [
        account
        for account in accounts
        if account.get("billing_street")
        and candidate_for(location, account) is not None
    ]

    if candidates:
        names = ", ".join(
            f"{account['name']} ({account['account_id']})"
            for account in candidates
        )
        raise ValueError(
            "The live CRM contains possible matches: "
            + names
            + ". Review them before creating an account."
        )


def verify_parent(accounts, parent_id):
    matches = [
        account
        for account in accounts
        if account["account_id"] == parent_id
    ]

    if len(matches) != 1:
        raise ValueError("The proposed parent account was not found.")

    parent = matches[0]

    if (
        normalize(parent["name"])
        != normalize("Bellhaven Senior Living (Parent Account)")
        or parent.get("parent_id")
        or parent.get("status") != "Active"
    ):
        raise ValueError("The proposed Bellhaven parent needs verification.")


def create_account(decision, location, payload, marker):
    require_current_finding(decision)
    # Repeat checks immediately before writing.
    verify_website(location)
    accounts = fetch_all_accounts()
    verify_no_match(location, accounts, marker)
    verify_parent(accounts, payload["parent_id"])

    final_proposal = {
        "operations": [{"type": "create", "fields": payload}],
        "source_url": location["source_url"],
        "care_offerings": location["care_offerings"],
    }

    # Save the exact approved payload and claim the operation atomically.
    with connect() as connection:
        result = connection.execute(
            """
            UPDATE decisions
            SET status = 'creating', proposal_json = ?
            WHERE item_id = ? AND version = ? AND status = 'prepared'
            """,
            (
                json.dumps(final_proposal),
                decision["item_id"],
                decision["version"],
            ),
        )

        if result.rowcount != 1:
            raise ValueError(
                "This proposal is no longer prepared. Refresh the page."
            )

    try:
        response = session.post(
            f"{BASE_URL}/api/v1/accounts",
            json=payload,
            timeout=30,
            allow_redirects=False,
        )

        if not 200 <= response.status_code < 300:
            raise ValueError(
                f"CRM returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        # Verify through a fresh CRM read, not just the POST response.
        after = fetch_all_accounts()

        matches = [
            account
            for account in after
            if marker in account.get("note", "")
        ]

        if len(matches) != 1:
            raise ValueError(
                "Could not verify exactly one new account with "
                "the creation reference."
            )

        created = matches[0]

        differences = [
            field
            for field, expected in payload.items()
            if created.get(field) != expected
        ]

        if differences:
            raise ValueError(
                "Created account differs from the approved payload: "
                + ", ".join(differences)
            )

        if (
            money(created.get("lifetime_revenue")) != 0
            or money(created.get("outstanding_ar")) != 0
        ):
            raise ValueError(
                "Unexpected billing values on the new account."
            )

        # Confirm the creation did not modify an existing account.
        before_by_id = {
            account["account_id"]: account for account in accounts
        }
        after_by_id = {
            account["account_id"]: account for account in after
        }

        for account_id, original in before_by_id.items():
            current = after_by_id.get(account_id)

            if current is None:
                raise ValueError("An existing account disappeared.")

            if any(
                current.get(key) != value
                for key, value in original.items()
                if key != "updated_at"
            ):
                raise ValueError(
                    "An existing account changed during creation. "
                    "Verify the concurrent change before continuing."
                )

    except Exception:
        set_status(decision, "needs_check")
        raise

    set_status(decision, "applied")
    return created


decisions = list_decisions()

if any(
    decision["status"] in {"creating", "applying", "needs_check", "chow_pending", "duplicate_pending"}
    for decision in decisions
):
    st.error(
        "An earlier write needs verification. "
        "Open recovery_app.py to verify the result without resending it."
    )
    st.stop()

prepared = []

for decision in decisions:
    if decision["status"] != "prepared":
        continue

    proposal = json.loads(decision["proposal_json"])
    operations = proposal.get("operations", [])

    if len(operations) == 1 and operations[0]["type"] == "create":
        prepared.append(decision)

if not prepared:
    st.info("No prepared account-creation proposals remain.")
    st.stop()

selected = st.selectbox(
    "Facility to create",
    range(len(prepared)),
    format_func=lambda index: json.loads(
        prepared[index]["finding_json"]
    )["location"]["name"],
)

decision = prepared[selected]
finding = json.loads(decision["finding_json"])
location = finding["location"]

proposal = json.loads(decision["proposal_json"])
payload = dict(proposal["operations"][0]["fields"])

offerings = location["care_offerings"]

from care_mapping import choose_care_type
payload["care_type"] = choose_care_type(
    offerings, decision["item_id"] + decision["version"]
)

marker = (
    f"[bellhaven-review:{decision['item_id']}:{decision['version']}]"
)

payload["note"] = (
    f"{marker}\n"
    f"Created after review by {decision['reviewer']}.\n"
    f"Source: {location['source_url']}\n"
    f"Website care offerings: {', '.join(offerings)}.\n"
    f"Reason: {decision['reason']}"
)

st.write("Review reason:", decision["reason"])
st.link_button("Open facility source", location["source_url"])

st.caption(
    "For this review, Short-Term Rehabilitation & Nursing maps to "
    "the CRM's Skilled Nursing label. The original offering is "
    "preserved in the note."
)

st.subheader("Exact account fields to create")
st.json(payload)

try:
    verify_website(location)
    live_accounts = fetch_all_accounts()
    verify_no_match(location, live_accounts, marker)
    verify_parent(live_accounts, payload["parent_id"])
except Exception as error:
    st.error(str(error))
    st.stop()

st.success(
    "The website still agrees with the reviewed evidence. "
    "No supported live CRM match was found, and the Bellhaven "
    "parent account is valid."
)

confirmed = st.checkbox(
    "I approve creating this account with the fields shown above.",
    key=f"confirm-{decision['item_id']}",
)

if st.button(
    "Approve and create",
    type="primary",
    disabled=not confirmed,
):
    try:
        created = create_account(decision, location, payload, marker)
    except Exception as error:
        st.error(str(error))
        st.warning(
            "Do not click again or reset the decision. "
            "Send the error so we can verify whether creation succeeded."
        )
    else:
        st.success("The new account was created and verified.")
        st.json(created)