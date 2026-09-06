"""Reconcile uncertain writes using GET only. No POST/PATCH retry here."""
import json
from decision_store import connect
from inspect_sources import get
from crm import fetch_all_accounts
from matcher import money

ALLOWED = {'needs_check', 'applying', 'creating'}

def read_account(account_id):
    value = get(f'/api/v1/accounts/{account_id}').json()
    if isinstance(value, dict) and isinstance(value.get('data'), dict):
        value = value['data']
    if not isinstance(value, dict) or value.get('account_id') != account_id:
        raise ValueError('Unexpected account response.')
    return value

def original_account(finding, account_id):
    accounts = [c['account'] for c in finding.get('strong_candidates', []) + finding.get('possible_candidates', [])]
    if 'account' in finding:
        accounts.append(finding['account'])
    return next(a for a in accounts if a['account_id'] == account_id)

def inspect_result(decision, read=read_account, fetch=fetch_all_accounts):
    proposal = json.loads(decision['proposal_json'])
    operations = proposal['operations']
    if len(operations) != 1:
        raise ValueError('Use the duplicate or CHOW screen for a multi-step operation.')
    operation = operations[0]
    fields = operation['fields']
    if operation['type'] == 'update':
        original = original_account(json.loads(decision['finding_json']), operation['account_id'])
        live = read(operation['account_id'])
        expected = {**original, **fields}
        ignored = {'updated_at'} | ({'parent_name'} if 'parent_id' in fields else set())
        if all(live.get(k) == v for k, v in expected.items() if k not in ignored):
            return 'applied', 'The exact approved update is present.', live
        if all(live.get(k) == v for k, v in original.items() if k != 'updated_at'):
            return 'prepared', 'The original state is present. Return to approval; fresh source checks run before an explicit retry.', live
        return None, 'Conflicting changes found. Keep this operation unresolved for investigation.', live
    if operation['type'] != 'create':
        raise ValueError('Use the dedicated recovery workflow for this operation.')
    marker = f"[bellhaven-review:{decision['item_id']}:{decision['version']}]"
    matches = [a for a in fetch() if marker in a.get('note', '')]
    if len(matches) != 1:
        return None, ('No uniquely identified creation found. No retry was sent. A missing result after a timeout does not prove the POST failed; investigate before retrying.'), matches
    live = matches[0]
    if (all(live.get(k) == v for k, v in fields.items())
        and money(live.get('lifetime_revenue')) == 0
        and money(live.get('outstanding_ar')) == 0
        and not live.get('duplicate_of_account')
        and not live.get('chow_current_account')):
        return 'applied', 'Exactly one account contains the creation reference and approved fields.', live
    return None, 'The referenced account conflicts with the approved payload. Investigate.', live

def record_recovery(decision):
    # Re-read when clicked, not just when previewed.
    status, explanation, live = inspect_result(decision)
    if status is None:
        raise ValueError(explanation)
    with connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS recovery_events (item_id TEXT, version TEXT, previous_status TEXT, new_status TEXT, evidence_json TEXT, recorded_at TEXT DEFAULT CURRENT_TIMESTAMP)')
        changed = db.execute("UPDATE decisions SET status = ? WHERE item_id = ? AND version = ? AND status = ?", (status, decision['item_id'], decision['version'], decision['status']))
        if decision['status'] not in ALLOWED or changed.rowcount != 1:
            raise ValueError('Decision changed during recovery. Refresh.')
        db.execute('INSERT INTO recovery_events (item_id, version, previous_status, new_status, evidence_json) VALUES (?, ?, ?, ?, ?)', (decision['item_id'], decision['version'], decision['status'], status, json.dumps(live)))
    return explanation
