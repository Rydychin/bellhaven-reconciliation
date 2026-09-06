import json
from datetime import datetime, timezone

from inspect_sources import DATA, get


def fetch_all_accounts():
    """Read every CRM account, checking pagination for completeness."""
    accounts = []
    seen_ids = set()
    expected_total = None
    page = 1

    while True:
        print(f"Downloading CRM page {page}...", flush=True)

        payload = get(
            f"/api/v1/accounts?page={page}&page_size=50"
        ).json()

        if not isinstance(payload, dict):
            raise ValueError("Expected an object from the accounts endpoint.")

        batch = payload.get("data")
        total = payload.get("total")

        if not isinstance(batch, list):
            raise ValueError("The response is missing its data list.")

        if not isinstance(total, int) or total < 0:
            raise ValueError("The response has an invalid total.")

        if payload.get("page") != page:
            raise ValueError("The API returned an unexpected page number.")

        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise ValueError(
                "The CRM account count changed during download. "
                "Run the script again to get a consistent snapshot."
            )

        for account in batch:
            account_id = account.get("account_id")

            if not account_id:
                raise ValueError("An account is missing account_id.")

            if account_id in seen_ids:
                raise ValueError(
                    f"Repeated account across pages: {account_id}"
                )

            seen_ids.add(account_id)
            accounts.append(account)

        print(f"  Retrieved {len(accounts)} of {expected_total}")

        if len(accounts) == expected_total:
            return accounts

        if len(accounts) > expected_total:
            raise ValueError("Downloaded more accounts than the API total.")

        if not batch:
            raise ValueError(
                "Pagination stopped before all accounts were downloaded."
            )

        page += 1


def main():
    accounts = fetch_all_accounts()

    timestamp = datetime.now(timezone.utc)
    snapshot = {
        "fetched_at": timestamp.isoformat(),
        "total": len(accounts),
        "accounts": accounts,
    }

    text = json.dumps(snapshot, indent=2, ensure_ascii=False)

    # Preserve each successful download as evidence.
    archive_path = DATA / (
        f"crm-accounts-{timestamp.strftime('%Y%m%dT%H%S%fZ')}.json"
    )
    archive_path.write_text(text, encoding="utf-8")

    # Replace the latest snapshot only after a complete download.
    latest_path = DATA / "crm-accounts.json"
    temporary_path = DATA / "crm-accounts.tmp"
    temporary_path.write_text(text, encoding="utf-8")
    temporary_path.replace(latest_path)

    print(f"\nSaved {len(accounts)} accounts to {latest_path}")

    print("\nAccounts with Bellhaven in their name:")
    for account in accounts:
        if "bellhaven" in account.get("name", "").lower():
            print(
                f"  {account['account_id']} | "
                f"{account['name']} | "
                f"Parent: {account.get('parent_id') or '(none)'}"
            )


if __name__ == "__main__":
    main()