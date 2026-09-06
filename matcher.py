import hashlib
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation

from inspect_sources import DATA


# Normalize spelling differences without changing the stored address.
STREET_WORDS = {
    "street": "st",
    "road": "rd",
    "avenue": "ave",
    "boulevard": "blvd",
    "drive": "dr",
    "lane": "ln",
    "court": "ct",
    "place": "pl",
    "parkway": "pkwy",
    "pike": "pike",
    "pk": "pike",
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northwest": "nw",
    "northeast": "ne",
    "southwest": "sw",
    "southeast": "se",
}


def normalize(value):
    return " ".join(
        re.findall(r"[a-z0-9]+", str(value or "").lower())
    )


def normalize_street(value):
    words = normalize(value).split()
    return " ".join(STREET_WORDS.get(word, word) for word in words)


def money(value):
    """Unknown billing values must not be treated as zero."""
    if value is None or isinstance(value, bool):
        return None

    try:
        amount = Decimal(str(value))
        return amount if amount.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def billing_route(account, parent_id):
    if account.get("parent_id") == parent_id:
        return "No parent change"

    revenue = money(account.get("lifetime_revenue"))
    ar = money(account.get("outstanding_ar"))

    if revenue is None or ar is None:
        return "Blocked: verify missing or invalid billing values"

    if revenue < 0 or ar < 0:
        return "Manual review: negative billing values"

    if revenue > 0 and ar > 0:
        return (
            "CHOW required: create a new account under Bellhaven; "
            "preserve the old account except for linking "
            "chow_current_account to the new account"
        )

    return "Direct re-parent permitted after approval"


def candidate_for(location, account):
    same_city = (
        normalize(location["city"])
        == normalize(account.get("billing_city"))
    )
    same_state = (
        normalize(location["state"])
        == normalize(account.get("billing_state"))
    )
    same_street = (
        normalize_street(location["street"])
        == normalize_street(account.get("billing_street"))
    )
    same_name = (
        normalize(location["name"])
        == normalize(account.get("name"))
    )
    same_zip = (
        location["zip"][:5]
        == str(account.get("billing_zip", ""))[:5]
    )

    # Geographic agreement is mandatory for an address match.
    if same_city and same_state and same_street:
        level = "strong"
        reasons = ["Same normalized street, city, and state"]

        if same_zip:
            reasons.append("ZIP agrees")
        else:
            reasons.append("ZIP differs: inspect proposed correction")

    elif same_city and same_state and same_name:
        level = "review"
        reasons = [
            "Same name, city, and state",
            "Street differs: do not assume the billing address is wrong",
        ]

    else:
        # Surface possible street typos without claiming a match.
        website_number = re.match(r"^\d+\b", location["street"])
        crm_number = re.match(
            r"^\d+\b",
            str(account.get("billing_street", "")),
        )

        same_number = (
            website_number is not None
            and crm_number is not None
            and website_number.group() == crm_number.group()
        )

        if not (same_city and same_state and same_number):
            return None

        level = "review"
        reasons = [
            "Same city, state, and street number",
            "Street text differs: manual identity review required",
        ]

    if same_name:
        reasons.append("Name agrees")
    else:
        reasons.append("Name differs: possible former name or duplicate")

    return {
        "level": level,
        "reasons": reasons,
        "account": account,
    }


def proposed_fields(location, account, parent_id):
    """Only propose identity/ownership fields supported by this match."""
    changes = {}

    if account.get("name") != location["name"]:
        changes["name"] = location["name"]

    if account.get("parent_id") != parent_id:
        changes["parent_id"] = parent_id

    if str(account.get("billing_zip", "")) != location["zip"]:
        changes["billing_zip"] = location["zip"]

    # Equivalent street abbreviations do not need cosmetic updates.
    # A differing street requires review, not automatic replacement.
    # Care offerings remain in the evidence until CRM values are mapped.
    return changes


def stable_id(kind, key):
    value = json.dumps([kind, key], sort_keys=True)
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def main():
    website = json.loads(
        (DATA / "website-locations.json").read_text(encoding="utf-8")
    )
    crm = json.loads(
        (DATA / "crm-accounts.json").read_text(encoding="utf-8")
    )

    if website.get("validation", {}).get("complete") is not True:
        raise ValueError("A complete website scrape is required.")

    locations = website["locations"]
    accounts = crm["accounts"]

    if len(locations) != website["total"]:
        raise ValueError("Website snapshot count is inconsistent.")

    if len(accounts) != crm["total"]:
        raise ValueError("CRM snapshot count is inconsistent.")

    ids = [account["account_id"] for account in accounts]
    if len(ids) != len(set(ids)):
        raise ValueError("CRM snapshot contains repeated account IDs.")

    parents = [
        account
        for account in accounts
        if normalize(account["name"])
        == normalize("Bellhaven Senior Living (Parent Account)")
        and not account.get("parent_id")
    ]

    if len(parents) != 1:
        raise ValueError("Could not identify exactly one Bellhaven parent.")

    parent_id = parents[0]["account_id"]

    # Exclude corporate records from facility matching.
    facilities = [
        account
        for account in accounts
        if account.get("billing_street")
    ]

    rows = []
    associated_ids = set()

    for location in locations:
        strong = []
        possible = []
        historical = []

        for account in facilities:
            candidate = candidate_for(location, account)

            if candidate is None:
                continue

            associated_ids.add(account["account_id"])
            candidate["billing_route"] = billing_route(account, parent_id)
            candidate["proposed_fields"] = proposed_fields(
                location, account, parent_id
            )

            # Previously linked old accounts are evidence, not new targets.
            if (
                account.get("chow_current_account")
                or account.get("duplicate_of_account")
            ):
                historical.append(candidate)
            elif candidate["level"] == "strong":
                strong.append(candidate)
            else:
                possible.append(candidate)

        if len(strong) > 1:
            classification = "duplicate_review"
            explanation = (
                "Multiple unlinked accounts share this facility address. "
                "Review their billing history and choose a survivor."
            )

        elif len(strong) == 1:
            candidate = strong[0]
            account = candidate["account"]
            route = candidate["billing_route"]

            if possible:
                classification = "identity_review"
                explanation = (
                    "One strong match and additional possible matches. "
                    "Resolve the other candidates before applying changes."
                )
            elif account.get("status") != "Active":
                classification = "identity_review"
                explanation = (
                    "The address matches, but the account is not Active."
                )
            elif route.startswith(("Blocked:", "Manual review:")):
                classification = "billing_review"
                explanation = route
            elif route.startswith("CHOW required:"):
                classification = "chow_required"
                explanation = route
            elif candidate["proposed_fields"]:
                classification = "needs_fix"
                explanation = (
                    "One strong address match with proposed field changes."
                )
            else:
                classification = "confident_match"
                explanation = (
                    "One strong match; identity and parent fields agree."
                )

        elif possible:
            classification = "identity_review"
            explanation = (
                "Possible match, but the street evidence is insufficient."
            )

        elif historical:
            classification = "linked_history_review"
            explanation = (
                "Only historical linked accounts match. Inspect their "
                "successors before proposing a new account."
            )

        else:
            classification = "no_crm_match"
            explanation = (
                "No address-supported CRM match found. "
                "Review before proposing account creation."
            )

        # Show same-name accounts elsewhere as rejected match evidence.
        excluded_names = [
            account
            for account in facilities
            if normalize(account["name"]) == normalize(location["name"])
            and (
                normalize(account.get("billing_city"))
                != normalize(location["city"])
                or normalize(account.get("billing_state"))
                != normalize(location["state"])
            )
        ]

        rows.append({
            "item_id": stable_id("location", location["source_url"]),
            "classification": classification,
            "explanation": explanation,
            "location": location,
            "strong_candidates": strong,
            "possible_candidates": possible,
            "historical_candidates": historical,
            "same_name_elsewhere": excluded_names,
        })

    # An account considered for any location must not also be labeled absent.
    absent = []

    for account in accounts:
        if (
            account.get("parent_id") == parent_id
            and account["account_id"] not in associated_ids
            and not account.get("chow_current_account")
            and not account.get("duplicate_of_account")
        ):
            absent.append({
                "item_id": stable_id("absent", account["account_id"]),
                "classification": "not_on_website",
                "account": account,
                "explanation": (
                    "Under Bellhaven in CRM but not matched to the complete "
                    "website inventory. Absence does not prove closure "
                    "or identify a new owner."
                ),
                "suggested_action": (
                    "Review ownership; consider Needs Review with a note. "
                    "Do not automatically deactivate or remove the parent."
                ),
            })

    # Detect whether a candidate is being assigned to multiple locations.
    assignments = {}

    for row in rows:
        for candidate in (
            row["strong_candidates"] + row["possible_candidates"]
        ):
            account_id = candidate["account"]["account_id"]
            assignments.setdefault(account_id, set()).add(row["item_id"])

    conflicts = {
        account_id
        for account_id, item_ids in assignments.items()
        if len(item_ids) > 1
    }

    for row in rows:
        candidate_ids = {
            candidate["account"]["account_id"]
            for candidate in (
                row["strong_candidates"] + row["possible_candidates"]
            )
        }

        if candidate_ids & conflicts:
            row["classification"] = "identity_review"
            row["explanation"] = (
                "A candidate also matches another website location. "
                "Resolve the conflict before making changes."
            )

    counts = dict(Counter(row["classification"] for row in rows))

    report = {
        "parent_id": parent_id,
        "website_scraped_at": website["scraped_at"],
        "crm_fetched_at": crm["fetched_at"],
        "summary": counts,
        "locations": rows,
        "not_on_website": absent,
    }

    temporary = DATA / "matching-report.tmp"
    temporary.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(DATA / "matching-report.json")

    print("\nMATCHING SUMMARY")
    for category, count in sorted(counts.items()):
        print(f"  {category}: {count}")

    print(f"  not_on_website: {len(absent)}")

    print("\nLOCATION RESULTS")
    for row in rows:
        print(
            f"  {row['classification']}: "
            f"{row['location']['name']}"
        )

    print("\nBELLHAVEN ACCOUNTS NOT FOUND ON WEBSITE")
    for row in absent:
        print(f"  {row['account']['name']}")

    print("\nSaved data/matching-report.json")
    print("No CRM changes were made.")


if __name__ == "__main__":
    main()