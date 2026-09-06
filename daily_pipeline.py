import fcntl
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from decision_store import list_decisions
from review_queue import pending_findings


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def main():
    DATA.mkdir(exist_ok=True)

    with (DATA / "pipeline.lock").open("a") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another pipeline run is already active.")

        started = datetime.now(timezone.utc)
        print(f"Pipeline started: {started.isoformat()}", flush=True)

        for script in ["scraper.py", "crm.py", "matcher.py"]:
            subprocess.run(
                [sys.executable, str(ROOT / script)],
                cwd=ROOT,
                check=True,
            )

        report = json.loads(
            (DATA / "matching-report.json").read_text(encoding="utf-8")
        )

        decisions = list_decisions()
        pending = pending_findings(report, decisions)

        unfinished_statuses = {
            "prepared",
            "applying",
            "creating",
            "needs_check",
            "chow_pending",
            "duplicate_pending",
        }

        unfinished = Counter(
            decision["status"]
            for decision in decisions
            if decision["status"] in unfinished_statuses
        )

        summary = {
            "started_at": started.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "website_locations": len(report["locations"]),
            "matching_summary": report["summary"],
            "pending_review_count": len(pending),
            "pending_item_ids": [
                finding["item_id"] for finding in pending
            ],
            "unfinished_decisions": dict(unfinished),
            "crm_writes_performed": 0,
        }

        text = json.dumps(summary, indent=2)

        temporary = DATA / "pipeline-summary.tmp"
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(DATA / "pipeline-summary.json")

        archive = DATA / "pipeline-runs"
        archive.mkdir(exist_ok=True)

        (archive / started.strftime("%Y%m%dT%H%S%fZ.json")).write_text(
            text,
            encoding="utf-8",
        )

        print("\nDAILY PIPELINE RESULT")
        print(text)


if __name__ == "__main__":
    main()