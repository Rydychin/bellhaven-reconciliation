import json
import unittest
from copy import deepcopy

from decision_store import finding_version
from review_queue import pending_findings


class ReviewQueueTests(unittest.TestCase):
    def setUp(self):
        self.finding = {
            "item_id": "example-absent-account",
            "classification": "not_on_website",
            "account": {
                "account_id": "account-1",
                "name": "Example Facility",
                "parent_id": "bellhaven",
                "status": "Active",
                "note": "",
                "outstanding_ar": 0,
                "updated_at": "original-time",
            },
        }

    def decision(self, status, proposal=None):
        return {
            "item_id": self.finding["item_id"],
            "version": finding_version(self.finding),
            "status": status,
            "finding_json": json.dumps(self.finding),
            "proposal_json": json.dumps(proposal or {}),
        }

    def report(self, finding):
        return {"locations": [], "not_on_website": [finding]}

    def test_rejection_survives_timestamp_change(self):
        current = deepcopy(self.finding)
        current["account"]["updated_at"] = "new-time"

        self.assertEqual(
            pending_findings(
                self.report(current),
                [self.decision("rejected")],
            ),
            [],
        )

    def test_changed_parent_requires_review(self):
        current = deepcopy(self.finding)
        current["account"]["parent_id"] = "another-parent"

        self.assertEqual(
            len(pending_findings(
                self.report(current),
                [self.decision("rejected")],
            )),
            1,
        )

    def test_applied_absence_flag_does_not_repeat(self):
        fields = {
            "status": "Needs Review",
            "note": "Approved ownership investigation note.",
        }

        proposal = {
            "operations": [{
                "type": "update",
                "account_id": "account-1",
                "fields": fields,
            }]
        }

        current = deepcopy(self.finding)
        current["account"].update(fields)
        current["account"]["updated_at"] = "after-update"

        self.assertEqual(
            pending_findings(
                self.report(current),
                [self.decision("applied", proposal)],
            ),
            [],
        )

        # A later, unapproved change must reopen the finding.
        current["account"]["note"] = "An unexpected replacement note."

        self.assertEqual(
            len(pending_findings(
                self.report(current),
                [self.decision("applied", proposal)],
            )),
            1,
        )

    def test_prepared_proposal_does_not_repeat(self):
        self.assertEqual(
            pending_findings(
                self.report(self.finding),
                [self.decision("prepared")],
            ),
            [],
        )

    def test_confident_match_needs_no_manual_decision(self):
        report = {
            "locations": [{
                "item_id": "matched-location",
                "classification": "confident_match",
            }],
            "not_on_website": [],
        }

        self.assertEqual(pending_findings(report, []), [])


if __name__ == "__main__":
    unittest.main()