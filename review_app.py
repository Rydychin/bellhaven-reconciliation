import json
from decimal import Decimal

import streamlit as st

from decision_store import (
    list_decisions,
    save_decision,
)
from review_queue import (
    decision_for_finding as get_decision,
    pending_findings,
)
from inspect_sources import DATA
from matcher import billing_route, money


st.set_page_config(
    page_title="Bellhaven CRM Review",
    page_icon="🏡",
    layout="wide",
)

st.title("Bellhaven CRM Review")
st.caption(
    "Review evidence and prepare corrections. "
    "This version saves locally and makes no CRM writes."
)

report_path = DATA / "matching-report.json"

if not report_path.exists():
    st.error("Run python matcher.py first.")
    st.stop()

report = json.loads(report_path.read_text(encoding="utf-8"))
parent_id = report["parent_id"]

findings = report["locations"] + report["not_on_website"]


def title(finding):
    if "location" in finding:
        return finding["location"]["name"]
    return finding["account"]["name"]


def new_account_fields(location):
    # Care-type encoding will be resolved before API writeback.
    # Do not guess how the CRM accepts multiple care offerings.
    return {
        "name": location["name"],
        "parent_id": parent_id,
        "billing_street": location["street"],
        "billing_city": location["city"],
        "billing_state": location["state"],
        "billing_zip": location["zip"],
        "phone": location.get("phone", ""),
        "status": "Active",
    }


def append_note(account, message):
    existing = account.get("note", "").strip()
    return f"{existing}\n{message}".strip()


def proposal_for_candidate(candidate, location):
    account = candidate["account"]
    route = billing_route(account, parent_id)

    if route.startswith(("Blocked:", "Manual review:")):
        raise ValueError(route)

    if route.startswith("CHOW required:"):
        return [{
            "type": "chow",
            "old_account_id": account["account_id"],
            "create_fields": new_account_fields(location),
            "after_create": {
                "instruction": (
                    "Set only chow_current_account on the old account "
                    "to the new account ID. Preserve all other old fields."
                )
            },
        }]

    fields = candidate["proposed_fields"]

    if not fields:
        return []

    return [{
        "type": "update",
        "account_id": account["account_id"],
        "fields": fields,
    }]


reviewer = st.sidebar.text_input("Reviewer name", value="Ryan")

view = st.sidebar.radio(
    "View",
    ["Pending review", "Decision history"],
)

pending = pending_findings(report)

st.sidebar.metric("Pending findings", len(pending))
st.sidebar.caption(
    "Prepared proposals still require approval and API writeback."
)

if view == "Decision history":
    history = list_decisions()

    if not history:
        st.info("No decisions saved yet.")

    for decision in history:
        finding = json.loads(decision["finding_json"])

        with st.expander(
            f"{decision['status']} · {title(finding)}"
        ):
            st.write("Reviewer:", decision["reviewer"])
            st.write("Reason:", decision["reason"])
            st.write("Recorded:", decision["decided_at"])

            current = next(
                (
                    item
                    for item in findings
                    if item["item_id"] == decision["item_id"]
                ),
                None,
            )

            if current is None:
                st.caption("This item is absent from the current report.")
            else:
                current_decision = get_decision(current)
                if (
                    current_decision is None
                    or current_decision["version"] != decision["version"]
                ):
                    st.warning(
                        "Historical decision: the current finding differs."
                    )

            if decision["proposal_json"]:
                st.json(json.loads(decision["proposal_json"]))

    st.stop()

if not pending:
    st.success(
        "No unresolved findings remain in this report. "
        "Check Decision history for saved proposals and applied changes."
    )
    st.stop()

selected_id = st.selectbox(
    "Choose a finding",
    options=[finding["item_id"] for finding in pending],
    format_func=lambda item_id: next(
        f"{item['classification']} · {title(item)}"
        for item in pending
        if item["item_id"] == item_id
    ),
)

finding = next(
    item for item in pending if item["item_id"] == selected_id
)

st.subheader(title(finding))
st.write(finding["explanation"])

left, right = st.columns(2)

with left:
    st.markdown("**Website evidence**")

    if "location" in finding:
        location = finding["location"]

        st.write(
            f"{location['street']}, "
            f"{location['city']}, "
            f"{location['state']} {location['zip']}"
        )
        st.write("Care offerings:", ", ".join(location["care_offerings"]))
        st.link_button("Open source page", location["source_url"])

        with st.expander("Captured page text"):
            st.write(location["evidence_text"])

    else:
        st.write("No match in the complete 35-location snapshot.")
        st.caption(
            "This does not establish closure or a new owner."
        )

with right:
    st.markdown("**CRM evidence**")

    candidates = (
        finding.get("strong_candidates", [])
        + finding.get("possible_candidates", [])
    )

    if "account" in finding:
        st.json(finding["account"])

    for candidate in candidates:
        account = candidate["account"]

        with st.expander(
            f"{account['name']} · {account['account_id']}",
            expanded=len(candidates) == 1,
        ):
            st.json(account)
            st.write("Match evidence:", candidate["reasons"])
            st.write("Billing route:", candidate["billing_route"])

    for candidate in finding.get("historical_candidates", []):
        with st.expander("Historical linked account"):
            st.json(candidate)

    if finding.get("same_name_elsewhere"):
        with st.expander("Same-name accounts at other locations"):
            st.json(finding["same_name_elsewhere"])

classification = finding["classification"]
operations = None
blocked = None

if classification == "confident_match":
    st.info("No identity or ownership correction is proposed.")

elif classification in {"needs_fix", "chow_required"}:
    try:
        operations = proposal_for_candidate(
            finding["strong_candidates"][0],
            finding["location"],
        )
    except ValueError as error:
        blocked = str(error)

elif classification == "no_crm_match":
    operations = [{
        "type": "create",
        "fields": new_account_fields(finding["location"]),
        "care_offerings_to_map": finding["location"]["care_offerings"],
    }]

elif classification == "duplicate_review":
    candidates = finding["strong_candidates"]

    survivor_id = st.selectbox(
        "Choose the surviving account",
        options=[None] + [
            candidate["account"]["account_id"]
            for candidate in candidates
        ],
        format_func=lambda account_id: (
            "Select a survivor after reviewing every account"
            if account_id is None
            else next(
                f"{candidate['account']['name']} · {account_id}"
                for candidate in candidates
                if candidate["account"]["account_id"] == account_id
            )
        ),
    )

    if survivor_id:
        survivor = next(
            candidate
            for candidate in candidates
            if candidate["account"]["account_id"] == survivor_id
        )

        losers = [
            candidate["account"]
            for candidate in candidates
            if candidate["account"]["account_id"] != survivor_id
        ]

        # Billing-bearing duplicate records need a separate review path.
        if any(
            money(account.get("lifetime_revenue")) != Decimal("0")
            or money(account.get("outstanding_ar")) != Decimal("0")
            for account in losers
        ):
            blocked = (
                "A losing account has billing history or unknown billing "
                "values. Leave this group pending for a separate review."
            )
        elif billing_route(
            survivor["account"], parent_id
        ).startswith("CHOW required:"):
            blocked = (
                "The survivor requires CHOW. Leave this group pending "
                "until the combined workflow is implemented."
            )
        else:
            try:
                operations = proposal_for_candidate(
                    survivor, finding["location"]
                )

                for account in losers:
                    operations.append({
                        "type": "update",
                        "account_id": account["account_id"],
                        "fields": {
                            "status": "Inactive",
                            "duplicate_of_account": survivor_id,
                            "note": append_note(
                                account,
                                f"Reviewed duplicate of {survivor_id}. "
                                "Same facility address as "
                                f"{finding['location']['source_url']}.",
                            ),
                        },
                    })
            except ValueError as error:
                blocked = str(error)

elif classification == "not_on_website":
    account = finding["account"]

    operations = [{
        "type": "update",
        "account_id": account["account_id"],
        "fields": {
            "status": "Needs Review",
            "note": append_note(
                account,
                "Not found in the complete Bellhaven website inventory "
                f"scraped at {report['website_scraped_at']}. "
                "Verify current ownership; absence alone does not "
                "establish closure or a new parent.",
            ),
        },
    }]

else:
    st.warning(
        "This finding requires additional identity or billing investigation. "
        "Leave it pending unless you have a supported reason to reject it "
        "or record no change."
    )

if blocked:
    st.warning(blocked)

if operations is not None and not blocked:
    st.markdown("**Proposed operations — not yet approved**")
    st.json(operations)

    st.caption(
        "The next stage will validate API fields and recheck live CRM "
        "records before approval. Creation proposals still need care-type "
        "mapping."
    )

reason = st.text_area(
    "Review reason",
    placeholder="Explain the evidence supporting your decision.",
    key=f"reason-{selected_id}",
)

confirmed = st.checkbox(
    "I reviewed the evidence and the proposed operations.",
    key=f"confirmed-{selected_id}",
)

can_save = bool(reviewer.strip() and reason.strip() and confirmed)

prepare, reject, no_change = st.columns(3)

with prepare:
    if st.button(
        "Save proposal for approval",
        disabled=(
            not can_save
            or not operations
            or bool(blocked)
        ),
    ):
        save_decision(
            finding,
            "prepared",
            reviewer,
            reason,
            proposal={
                "operations": operations,
                "source_url": finding.get("location", {}).get("source_url"),
                "care_offerings": finding.get("location", {}).get(
                    "care_offerings", []
                ),
            },
        )
        st.rerun()

with reject:
    if st.button("Reject finding", disabled=not can_save):
        save_decision(
            finding, "rejected", reviewer, reason
        )
        st.rerun()

with no_change:
    if st.button("Record no change", disabled=not can_save):
        save_decision(
            finding, "no_change", reviewer, reason
        )
        st.rerun()