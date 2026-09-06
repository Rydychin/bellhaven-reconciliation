"""Refresh sources before a new approval. Never used to resume partial writes."""
import json
import subprocess
import sys
from pathlib import Path
from decision_store import list_decisions
from review_queue import same_evidence

ROOT = Path(__file__).resolve().parent

def validate_current_finding(decision, report, decisions):
    related = [d for d in decisions if d['item_id'] == decision['item_id']]
    if not related or related[0]['version'] != decision['version'] or related[0]['status'] != 'prepared':
        raise ValueError('This proposal is no longer the current prepared decision. Return to review.')
    saved = json.loads(decision['finding_json'])
    current = next((f for f in report['locations'] + report['not_on_website'] if f['item_id'] == decision['item_id']), None)
    if current is None or not same_evidence(saved, current):
        raise ValueError('Source evidence or matching changed since preparation. No write was sent. Open the review app and review the current finding.')

def require_current_finding(decision):
    # The existing pipeline checks full website membership and all CRM pages.
    # A failed or incomplete scrape prevents approval, including absence flags.
    subprocess.run([sys.executable, str(ROOT / 'daily_pipeline.py')], cwd=ROOT, check=True, capture_output=True, text=True)
    report = json.loads((ROOT / 'data/matching-report.json').read_text())
    validate_current_finding(decision, report, list_decisions())
