import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import decision_store
from decision_store import finding_version
from review_queue import pending_findings
from approval_safety import validate_current_finding
from recovery import inspect_result
from care_mapping import suggested_care_type

class SubmissionFixTests(unittest.TestCase):
    def setUp(self):
        self.account = {'account_id':'a', 'name':'Facility', 'parent_id':'old', 'status':'Active', 'note':'', 'lifetime_revenue':0, 'outstanding_ar':0}
        self.finding = {'item_id':'item', 'classification':'needs_fix', 'location':{'name':'Facility'}, 'strong_candidates':[{'account':self.account}]}
        self.proposal = {'operations':[{'type':'update','account_id':'a','fields':{'parent_id':'new'}}]}
        self.decision = {'item_id':'item','version':finding_version(self.finding),'status':'applied','finding_json':json.dumps(self.finding),'proposal_json':json.dumps(self.proposal)}
        self.report = {'locations':[self.finding],'not_on_website':[]}
    def test_applied_error_recurrence_reopens(self):
        pending = pending_findings(self.report,[self.decision])
        self.assertEqual(len(pending),1)
        self.assertNotEqual(finding_version(pending[0]),self.decision['version'])
    def test_reopened_decision_can_be_saved_without_primary_key_collision(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(decision_store,'DATABASE',Path(tmp)/'test.sqlite3'):
            decision_store.save_decision(self.finding,'prepared','test','initial',self.proposal)
            with decision_store.connect() as db:
                db.execute("UPDATE decisions SET status='applied'")
            pending = pending_findings(self.report)[0]
            decision_store.save_decision(pending,'prepared','test','regression',self.proposal)
            self.assertEqual(len(decision_store.list_decisions()),2)
            self.assertEqual(pending_findings(self.report),[])
    def test_supersede_old_prepared_proposal(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(decision_store,'DATABASE',Path(tmp)/'test.sqlite3'):
            decision_store.save_decision(self.finding,'prepared','test','initial',self.proposal)
            changed=deepcopy(self.finding); changed['location']['name']='Changed'
            decision_store.save_decision(changed,'prepared','test','updated',self.proposal)
            self.assertEqual({d['status'] for d in decision_store.list_decisions()},{'prepared','superseded'})
    def test_current_approval_passes(self):
        d={**self.decision,'status':'prepared'}
        validate_current_finding(d,self.report,[d])
    def test_removed_facility_blocks_approval(self):
        d={**self.decision,'status':'prepared'}
        with self.assertRaises(ValueError): validate_current_finding(d,{'locations':[],'not_on_website':[]},[d])
    def test_changed_evidence_blocks_approval(self):
        d={**self.decision,'status':'prepared'}
        current=deepcopy(self.report);current['locations'][0]['location']['name']='Different'
        with self.assertRaises(ValueError): validate_current_finding(d,current,[d])
    def test_superseded_approval_blocks(self):
        d={**self.decision,'status':'prepared'}
        with self.assertRaises(ValueError): validate_current_finding(d,self.report,[{**d,'version':'later'},d])
    def test_uncertain_update_present(self):
        live={**self.account,'parent_id':'new'}
        self.assertEqual(inspect_result(self.decision,read=lambda _:live)[0],'applied')
    def test_uncertain_update_unchanged(self):
        self.assertEqual(inspect_result(self.decision,read=lambda _:self.account)[0],'prepared')
    def test_uncertain_update_conflict(self):
        self.assertIsNone(inspect_result(self.decision,read=lambda _:{**self.account,'parent_id':'third'})[0])
    def creation(self):
        marker=f"[bellhaven-review:item:{self.decision['version']}]"
        fields={'name':'Facility','parent_id':'new','note':marker}
        return {**self.decision,'proposal_json':json.dumps({'operations':[{'type':'create','fields':fields}]})},fields
    def test_uncertain_creation_present(self):
        d,f=self.creation(); live={**self.account,**f}
        self.assertEqual(inspect_result(d,fetch=lambda:[live])[0],'applied')
    def test_uncertain_creation_missing_never_retries(self):
        d,_=self.creation()
        self.assertIsNone(inspect_result(d,fetch=lambda:[])[0])
    def test_uncertain_creation_multiple_blocks(self):
        d,f=self.creation(); live={**self.account,**f}
        self.assertIsNone(inspect_result(d,fetch=lambda:[live,live])[0])
    def test_assisted_living_supported(self):
        self.assertEqual(suggested_care_type(['Assisted Living']),'Assisted Living')
    def test_memory_supported(self):
        self.assertEqual(suggested_care_type(['Memory Support']),'Memory Care')
    def test_multiple_offerings_require_selection(self):
        self.assertIsNone(suggested_care_type(['Assisted Living','Memory Support']))
    def test_unknown_offering_requires_selection(self):
        self.assertIsNone(suggested_care_type(['New Offering']))

if __name__=='__main__': unittest.main()
