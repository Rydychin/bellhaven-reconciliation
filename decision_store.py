import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from inspect_sources import DATA


DATABASE = DATA / "review.sqlite3"

# These values change on each scrape without changing the finding.
VOLATILE_FIELDS = {
    "scraped_at",
    "fetched_at",
    "updated_at",
    "evidence_file",
    "evidence_sha256",
    "discovered_from",
}


def semantic_value(value):
    if isinstance(value, dict):
        return {
            key: semantic_value(item)
            for key, item in sorted(value.items())
            if key not in VOLATILE_FIELDS
        }

    if isinstance(value, list):
        items = [semantic_value(item) for item in value]
        return sorted(
            items,
            key=lambda item: json.dumps(item, sort_keys=True),
        )

    return value


def finding_version(finding):
    text = json.dumps(
        semantic_value(finding),
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(text.encode()).hexdigest()


def connect():
    connection = sqlite3.connect(DATABASE, timeout=30)
    connection.row_factory = sqlite3.Row

    connection.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            item_id TEXT NOT NULL,
            version TEXT NOT NULL,
            status TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            reason TEXT NOT NULL,
            proposal_json TEXT,
            finding_json TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            PRIMARY KEY (item_id, version)
        )
    """)

    return connection


def get_decision(finding):
    with connect() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM decisions
            WHERE item_id = ? AND version = ?
            """,
            (finding["item_id"], finding_version(finding)),
        ).fetchone()

    return dict(row) if row else None


def save_decision(finding, status, reviewer, reason, proposal=None):
    allowed = {"prepared", "rejected", "no_change"}

    if status not in allowed:
        raise ValueError("Unsupported review status.")

    if not reviewer.strip() or not reason.strip():
        raise ValueError("Enter your name and review reason.")

    with connect() as connection:
        connection.execute(
            "UPDATE decisions SET status = 'superseded' "
            "WHERE item_id = ? AND status = 'prepared'",
            (finding["item_id"],),
        )
        connection.execute(
            """
            INSERT INTO decisions (
                item_id,
                version,
                status,
                reviewer,
                reason,
                proposal_json,
                finding_json,
                decided_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finding["item_id"],
                finding_version(finding),
                status,
                reviewer.strip(),
                reason.strip(),
                (
                    json.dumps(proposal, ensure_ascii=False)
                    if proposal is not None
                    else None
                ),
                json.dumps(finding, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def list_decisions():
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM decisions ORDER BY decided_at DESC"
        ).fetchall()

    return [dict(row) for row in rows]