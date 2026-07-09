import unittest

from backend.app.core.schemas import ReviewRequest, ReviewRoute
from backend.app.services.prompt_builder import build_review_messages
from backend.app.services.publisher import format_review_markdown
from backend.app.services.llm import MockLLMClient
from backend.app.core.config import Settings


class PromptBuilderTest(unittest.TestCase):
    def test_review_prompt_requires_korean_human_readable_text(self):
        request = ReviewRequest.from_dict(
            {
                "repository": {"owner": "team", "name": "repo"},
                "pull_request": {
                    "number": 1,
                    "title": "Change API",
                    "author": "dev",
                    "base_sha": "base",
                    "head_sha": "head",
                    "base_branch": "main",
                    "head_branch": "feature",
                },
            }
        )
        route = ReviewRoute(
            name="policy_context_review",
            model_tier="solar3-medium",
            use_rag=True,
            focus=["repo_policy"],
            reasons=["repository policy is available"],
            confidence=0.9,
        )

        messages = build_review_messages(request, route, [])

        self.assertIn("Write all human-readable review text in Korean", messages[0]["content"])
        self.assertIn("한국어로 작성한 PR 전체 요약", messages[1]["content"])
        self.assertIn("한국어로 작성한 구체적인 문제 설명", messages[1]["content"])

    def test_review_markdown_uses_korean_labels(self):
        settings = Settings(llm_mode="mock")
        request = ReviewRequest.from_dict(
            {
                "repository": {"owner": "team", "name": "repo"},
                "pull_request": {
                    "number": 1,
                    "title": "Change API",
                    "author": "dev",
                    "base_sha": "base",
                    "head_sha": "head",
                    "base_branch": "main",
                    "head_branch": "feature",
                },
                "checks": [{"kind": "test", "status": "completed", "conclusion": "success"}],
                "changed_files": [{"path": "app.py", "additions": 1, "deletions": 0}],
            }
        )
        route = ReviewRoute(
            name="policy_context_review",
            model_tier="solar3-medium",
            use_rag=False,
            focus=["style"],
            reasons=["checks passed"],
            confidence=0.9,
        )
        summary, findings, usage = MockLLMClient(settings).generate_review(request, route, [], [])
        from backend.app.core.schemas import PullRequestFeatures, ReviewResult

        result = ReviewResult(
            review_run_id="run-1",
            status="completed",
            idempotency_key=request.idempotency_key(),
            summary=summary,
            findings=findings,
            route=route,
            features=PullRequestFeatures(
                syntax_status="unknown",
                lint_status="unknown",
                test_status="passed",
                changed_files_count=1,
                changed_lines=1,
                risk_files=[],
                policy_available=False,
                router_confidence=0.9,
            ),
            model_call=usage,
            retrieved_policies=[],
        )

        markdown = format_review_markdown(result)

        self.assertIn("- 라우트:", markdown)
        self.assertIn("- 리뷰 티어:", markdown)
        self.assertIn("### 리뷰 결과", markdown)
        self.assertIn("개선 제안:", markdown)


if __name__ == "__main__":
    unittest.main()
