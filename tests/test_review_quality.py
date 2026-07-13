import unittest

from backend.app.core.schemas import (
    PolicyChunk,
    ReviewFinding,
    ReviewKnowledgeCard,
    ReviewRequest,
    ReviewRoute,
)
from backend.app.services.review_quality import validate_and_rank_findings


class ReviewQualityTest(unittest.TestCase):
    def setUp(self):
        self.request = ReviewRequest.from_dict(
            {
                "repository": {"owner": "team", "name": "repo"},
                "pull_request": {"number": 1, "head_sha": "head"},
                "changed_files": [
                    {
                        "path": "app/service.py",
                        "additions": 3,
                        "deletions": 1,
                        "patch": "@@ -9,2 +9,4 @@\n old\n-removed\n+added\n+more\n tail",
                    }
                ],
            }
        )
        self.route = ReviewRoute(
            name="policy_context_review",
            model_tier="solar3-medium",
            use_rag=True,
            focus=["repo_policy"],
            reasons=["repository policy is available"],
            confidence=0.9,
        )
        self.policies = [
            PolicyChunk(
                source_path="security.md",
                section_title="Secret logging",
                content="Do not log tokens.",
                policy_type="security",
            )
        ]
        self.cards = [
            ReviewKnowledgeCard(
                card_id="secret-log-flow",
                title="Secret log flow",
                skill_id="security-boundary",
                check="Check token logging.",
                evidence_required="A token and logging sink.",
                false_positive_guard="Ignore redacted values.",
                severity_cap="medium",
            )
        ]

    def _finding(self, **overrides):
        payload = {
            "severity": "P1",
            "category": "security",
            "file_path": "app/service.py",
            "line_start": 10,
            "line_end": 10,
            "message": "토큰이 로그에 기록됩니다.",
            "suggestion": "로그 호출에서 토큰을 제거하세요.",
            "policy_source": "security.md",
            "confidence": 1.2,
        }
        payload.update(overrides)
        return ReviewFinding(**payload)

    def test_validates_line_policy_severity_and_confidence(self):
        findings, report = validate_and_rank_findings(
            self.request,
            self.route,
            self.policies,
            [self._finding()],
        )

        self.assertEqual(report["accepted"], 1)
        self.assertEqual(findings[0].severity, "high")
        self.assertEqual(findings[0].line_start, 10)
        self.assertEqual(findings[0].policy_source, "security.md#Secret logging")
        self.assertEqual(findings[0].confidence, 1.0)

    def test_drops_unknown_files_and_deduplicates(self):
        finding = self._finding()
        findings, report = validate_and_rank_findings(
            self.request,
            self.route,
            self.policies,
            [finding, finding, self._finding(file_path="missing.py")],
        )

        self.assertEqual(len(findings), 1)
        self.assertEqual(report["duplicate_dropped"], 1)
        self.assertEqual(report["unknown_file_dropped"], 1)

    def test_keeps_finding_but_removes_unverifiable_line_and_policy(self):
        findings, report = validate_and_rank_findings(
            self.request,
            self.route,
            self.policies,
            [self._finding(line_start=999, line_end=999, policy_source="invented.md")],
        )

        self.assertEqual(len(findings), 1)
        self.assertIsNone(findings[0].line_start)
        self.assertIsNone(findings[0].policy_source)
        self.assertEqual(report["invalid_line_removed"], 1)
        self.assertEqual(report["invalid_policy_source_removed"], 1)

    def test_validates_card_id_and_enforces_card_severity_cap(self):
        findings, report = validate_and_rank_findings(
            self.request,
            self.route,
            self.policies,
            [
                self._finding(knowledge_card_id="secret-log-flow"),
                self._finding(
                    message="두 번째 문제입니다.",
                    knowledge_card_id="invented-card",
                ),
            ],
            knowledge_cards=self.cards,
        )

        findings_by_message = {finding.message: finding for finding in findings}
        capped = findings_by_message["토큰이 로그에 기록됩니다."]
        self.assertEqual(capped.severity, "medium")
        self.assertEqual(capped.knowledge_card_id, "secret-log-flow")
        self.assertNotIn("두 번째 문제입니다.", findings_by_message)
        self.assertEqual(report["severity_capped_by_card"], 1)
        self.assertEqual(report["invalid_knowledge_card_dropped"], 1)
        self.assertEqual(report["invalid_knowledge_card_ids"], ["invented-card"])

    def test_drops_finding_without_card_when_harness_cards_are_selected(self):
        findings, report = validate_and_rank_findings(
            self.request,
            self.route,
            self.policies,
            [self._finding(knowledge_card_id=None)],
            knowledge_cards=self.cards,
        )

        self.assertEqual(findings, [])
        self.assertEqual(report["missing_knowledge_card_dropped"], 1)

    def test_drops_english_finding_before_publishing(self):
        findings, report = validate_and_rank_findings(
            self.request,
            self.route,
            self.policies,
            [
                self._finding(
                    message="Token is logged.",
                    suggestion="Remove the token from the log call.",
                )
            ],
        )

        self.assertEqual(findings, [])
        self.assertEqual(report["non_korean_finding_dropped"], 1)


if __name__ == "__main__":
    unittest.main()
