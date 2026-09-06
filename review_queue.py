"""Decision reuse is based on the approved result, not the old error."""
import json
from copy import deepcopy
from decision_store import finding_version, list_decisions

ACTIVE = {'applying', 'creating', 'needs_check', 'chow_pending', 'duplicate_pending'}

def base_finding(finding):
    result = deepcopy(finding)
    result.pop('_review_cycle', None)
    return result

def same_evidence(first, second):
    return finding_version(base_finding(first)) == finding_version(base_finding(second))

def decision_for_finding(finding, decisions=None):
    decisions = list_decisions() if decisions is None else decisions
    related = [d for d in decisions if d['item_id'] == finding['item_id'] and d['status'] != 'superseded']
    # Incomplete writes must be recovered, never turned into fresh proposals.
    for decision in related:
        if decision['status'] in ACTIVE:
            return decision
    for decision in related:
        previous = json.loads(decision['finding_json'])
        if decision['status'] in {'prepared', 'rejected', 'no_change'}:
            if same_evidence(previous, finding):
                return decision
            continue
        if decision['status'] != 'applied':
            continue
        # A matching BEFORE state means a correction has regressed.
        # Only the explicit approved AFTER state can resolve an absence.
        if finding['classification'] != 'not_on_website' or previous.get('classification') != 'not_on_website':
            continue
        operations = json.loads(decision['proposal_json'] or '{}').get('operations', [])
        if len(operations) != 1:
            continue
        operation = operations[0]
        fields = operation.get('fields', {})
        if (operation.get('type') != 'update'
            or operation.get('account_id') != previous['account']['account_id']
            or fields.get('status') != 'Needs Review'
            or not set(fields).issubset({'status', 'note'})):
            continue
        expected = deepcopy(previous)
        expected['account'].update(fields)
        if same_evidence(expected, finding):
            return decision
    return None

def pending_findings(report, decisions=None):
    decisions = list_decisions() if decisions is None else decisions
    pending = []
    for finding in report['locations'] + report['not_on_website']:
        if finding['classification'] == 'confident_match' or decision_for_finding(finding, decisions):
            continue
        result = deepcopy(finding)
        related = [d for d in decisions if d['item_id'] == finding['item_id']]
        if related:
            # Retain the original audit row; allocate a stable new review version.
            result['_review_cycle'] = related[0]['version']
        pending.append(result)
    return pending
