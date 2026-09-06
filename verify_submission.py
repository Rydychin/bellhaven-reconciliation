import json
from pathlib import Path

from decision_store import list_decisions
from review_queue import pending_findings


DATA = Path(__file__).resolve().parent / "data"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print("PASS:", message)


def main():
    current = load(DATA / "crm-accounts.json")
    report = load(DATA / "matching-report.json")

    archives = [
        load(path)
        for path in DATA.glob("crm-accounts-*.json")
    ]

    require(bool(archives), "An original CRM snapshot is available")

    original = min(archives, key=lambda snapshot: snapshot["fetched_at"])

    before = {
        account["account_id"]: account
        for account in original["accounts"]
    }
    after = {
        account["account_id"]: account
        for account in current["accounts"]
    }

    require(
        set(before).issubset(after),
        "Every original account still exists",
    )

    decisions = list_decisions()
    allowed_changes = {}
    chow_old_ids = set()
    expected_creations = 0

    for decision in decisions:
        if decision["status"] != "applied":
            continue

        proposal = json.loads(decision["proposal_json"])

        for operation in proposal["operations"]:
            kind = operation["type"]

            if kind == "update":
                account_id = operation["account_id"]
                fields = operation["fields"]

                allowed_changes.setdefault(account_id, set()).update(fields)

                if "parent_id" in fields:
                    allowed_changes[account_id].add("parent_name")

                require(
                    all(
                        after[account_id].get(field) == value
                        for field, value in fields.items()
                    ),
                    f"Approved update is retained for {account_id}",
                )

            elif kind == "create":
                expected_creations += 1

            elif kind == "chow":
                expected_creations += 1
                old_id = operation["old_account_id"]
                chow_old_ids.add(old_id)

                allowed_changes.setdefault(old_id, set()).add(
                    "chow_current_account"
                )

    for account_id, baseline in before.items():
        allowed = allowed_changes.get(account_id, set()) | {"updated_at"}

        unexpected = [
            field
            for field, value in baseline.items()
            if field not in allowed
            and after[account_id].get(field) != value
        ]

        require(
            not unexpected,
            f"No unapproved field changes on {account_id}",
        )

    new_ids = set(after) - set(before)

    require(
        len(new_ids) == expected_creations == 6,
        "Exactly six accounts were created: four missing facilities and two CHOW",
    )

    parent_id = report["parent_id"]

    for account_id in new_ids:
        account = after[account_id]

        require(
            account["parent_id"] == parent_id
            and account["status"] == "Active"
            and account["lifetime_revenue"] == 0
            and account["outstanding_ar"] == 0,
            f"New account {account_id} has Bellhaven parent and zero billing",
        )

    require(len(chow_old_ids) == 2, "Both CHOW operations were recorded")

    for old_id in chow_old_ids:
        old = after[old_id]
        new_id = old["chow_current_account"]

        require(
            new_id in new_ids,
            f"CHOW account {old_id} links to a newly created account",
        )

        require(
            all(
                old.get(field) == value
                for field, value in before[old_id].items()
                if field not in {"updated_at", "chow_current_account"}
            ),
            f"CHOW account {old_id} preserves all other original fields",
        )

    duplicates = [
        account
        for account in after.values()
        if account.get("duplicate_of_account")
    ]

    require(len(duplicates) == 7, "Seven losing duplicate copies are linked")

    for account in duplicates:
        survivor = after[account["duplicate_of_account"]]

        require(
            account["status"] == "Inactive"
            and survivor["status"] == "Active"
            and survivor["parent_id"] == parent_id
            and not survivor.get("duplicate_of_account"),
            f"Duplicate {account['account_id']} has a valid active survivor",
        )

    absent = report["not_on_website"]

    require(
        len(absent) == 3
        and all(
            row["account"]["status"] == "Needs Review"
            and row["account"]["note"]
            and row["account"]["parent_id"] == parent_id
            for row in absent
        ),
        "Three absent facilities are flagged with notes and retain their parent",
    )

    require(
        not pending_findings(report),
        "No unresolved review queue items remain",
    )

    require(
        all(
            decision["status"] in {"applied", "rejected", "no_change", "superseded"}
            for decision in decisions
        ),
        "No prepared or interrupted operations remain",
    )

    print("\nFINAL VERIFICATION PASSED")
    print("Original accounts:", len(before))
    print("Current accounts:", len(after))
    print("New accounts:", len(new_ids))
    print("Duplicate copies:", len(duplicates))
    print("CHOW cases:", len(chow_old_ids))


if __name__ == "__main__":
    main()