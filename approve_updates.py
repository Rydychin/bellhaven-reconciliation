from approval_safety import require_current_finding
import json

import streamlit as st

from decision_store import connect, list_decisions
from inspect_sources import BASE_URL, get, session
from matcher import billing_route


st.set_page_config(
    page_title="Approve Bellhaven Updates",
    page_icon="✅",
    layout="wide",
)

st.title("Approve CRM Updates")
st.warning(
    "This screen can change the CRM. "
    "Only clicking Approve and apply sends an update."
)


def read_account(account_id):
    payload = get(f"/api/v1/accounts/{account_id}").json()

    # Support either a direct account object or a data envelope.
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]

    if (
        not isinstance(payload, dict)
        or payload.get("account_id") != account_id
    ):
        raise ValueError("Unexpected response from the account endpoint.")

    return payload


def snapshot_account(finding, account_id):
    candidates = (
        finding.get("strong_candidates", [])
        + finding.get("possible_candidates", [])
    )

    accounts = [candidate["account"] for candidate in candidates]

    if "account" in finding:
        accounts.append(finding["account"])

    for account in accounts:
        if account["account_id"] == account_id:
            return account

    raise ValueError("The proposal has no original account snapshot.")


def validate_live_account(original, live, fields):
    # Compare against what the reviewer actually saw.
    # updated_at can change without a meaningful field change.
    changed = [
        key
        for key, value in original.items()
        if key != "updated_at" and live.get(key) != value
    ]

    if changed:
        raise ValueError(
            "CRM data changed since review: "
            + ", ".join(changed)
            + ". Refresh the pipeline and review the new finding."
        )

    if "parent_id" in fields:
        route = billing_route(live, fields["parent_id"])

        if route not in {
            "No parent change",
            "Direct re-parent permitted after approval",
        }:
            raise ValueError(
                "This screen cannot perform this ownership change: " + route
            )


def set_status(item_id, version, status):
    with connect() as connection:
        connection.execute(
            """
            UPDATE decisions
            SET status = ?
            WHERE item_id = ? AND version = ?
            """,
            (status, item_id, version),
        )


def apply_update(decision, original, operation):
    require_current_finding(decision)
    account_id = operation["account_id"]
    fields = operation["fields"]

    # Re-read immediately before writing, rather than trusting the preview.
    live = read_account(account_id)
    validate_live_account(original, live, fields)

    # Atomically claim this proposal so double clicks cannot apply it twice.
    with connect() as connection:
        result = connection.execute(
            """
            UPDATE decisions
            SET status = 'applying'
            WHERE item_id = ? AND version = ? AND status = 'prepared'
            """,
            (decision["item_id"], decision["version"]),
        )

        if result.rowcount != 1:
            raise ValueError(
                "This proposal is no longer prepared. Refresh the page."
            )

    try:
        response = session.patch(
            f"{BASE_URL}/api/v1/accounts/{account_id}",
            json=fields,
            timeout=30,
            allow_redirects=False,
        )

        if not 200 <= response.status_code < 300:
            raise ValueError(
                f"CRM returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        verified = read_account(account_id)

        if any(verified.get(key) != value for key, value in fields.items()):
            raise ValueError("The CRM did not retain all approved values.")

        # Verify that unrelated business fields stayed unchanged.
        allowed_changes = set(fields) | {"updated_at"}

        if "parent_id" in fields:
            allowed_changes.add("parent_name")

        unexpected = [
            key
            for key, value in live.items()
            if key not in allowed_changes and verified.get(key) != value
        ]

        if unexpected:
            raise ValueError(
                "Unexpected field changes: " + ", ".join(unexpected)
            )

    except Exception:
        # A timeout can occur after a successful remote write.
        # Never automatically resend an uncertain operation.
        set_status(
            decision["item_id"],
            decision["version"],
            "needs_check",
        )
        raise

    set_status(decision["item_id"], decision["version"], "applied")
    return verified


decisions = list_decisions()

uncertain = [
    decision
    for decision in decisions
    if decision["status"] in {"applying", "creating", "needs_check", "chow_pending", "duplicate_pending"}
]

if uncertain:
    st.error(
        "An earlier operation needs verification. "
        "Open recovery_app.py to verify the result without resending it."
    )

    for decision in uncertain:
        finding = json.loads(decision["finding_json"])
        st.write(
            finding.get("location", finding.get("account", {})).get("name"),
            decision["status"],
        )

    st.stop()

prepared = [
    decision for decision in decisions
    if decision["status"] == "prepared"
]

if not prepared:
    st.info("No prepared proposals remain.")

    for decision in decisions:
        if decision["status"] == "applied":
            finding = json.loads(decision["finding_json"])
            st.success(
                "Applied: "
                + finding.get("location", finding.get("account", {}))["name"]
            )

    st.stop()

selected = st.selectbox(
    "Prepared proposal",
    range(len(prepared)),
    format_func=lambda index: json.loads(
        prepared[index]["finding_json"]
    ).get(
        "location",
        json.loads(prepared[index]["finding_json"]).get("account", {}),
    )["name"],
)

decision = prepared[selected]
finding = json.loads(decision["finding_json"])
proposal = json.loads(decision["proposal_json"])
operations = proposal["operations"]

st.write("Reviewed by:", decision["reviewer"])
st.write("Review reason:", decision["reason"])
st.json(proposal)

if len(operations) != 1 or operations[0]["type"] != "update":
    st.info(
        "This proposal needs the creation, CHOW, or multi-update workflow. "
        "It cannot be applied from this screen yet."
    )
    st.stop()

operation = operations[0]
fields = operation["fields"]

# Limit this first writer to the fields used by our single-update proposals.
allowed_fields = {
    "parent_id",
    "name",
    "billing_zip",
    "status",
    "note",
}

if not fields or not set(fields).issubset(allowed_fields):
    st.error("This proposal contains unsupported fields.")
    st.stop()

try:
    original = snapshot_account(finding, operation["account_id"])
    live = read_account(operation["account_id"])
    validate_live_account(original, live, fields)
except Exception as error:
    st.error(str(error))
    st.stop()

st.subheader("Live CRM check")
st.write("Account:", live["name"])
st.write("Current parent:", live.get("parent_name"))
st.write("Lifetime revenue:", live.get("lifetime_revenue"))
st.write("Outstanding AR:", live.get("outstanding_ar"))

st.table([
    {
        "Field": key,
        "Current value": str(live.get(key, "")),
        "Approved value": str(value),
    }
    for key, value in fields.items()
])

confirmed = st.checkbox(
    "I approve these exact changes to this CRM account.",
    key=f"approve-{decision['item_id']}-{decision['version']}",
)

if st.button(
    "Approve and apply",
    type="primary",
    disabled=not confirmed,
):
    try:
        verified = apply_update(decision, original, operation)
    except Exception as error:
        st.error(str(error))
        st.warning(
            "If the proposal now shows needs_check or applying, "
            "do not retry. Send me the error so we can verify the CRM."
        )
    else:
        st.success("The update was applied and verified in the CRM.")
        st.json(verified)
        st.balloons()