import unittest

from backend.app.core.schemas import (
    GitHubPayload,
    ModelCallUsage,
    PullRequestFeatures,
    PullRequestPayload,
    RepositoryPayload,
    ReviewRequest,
    ReviewResult,
    ReviewRoute,
    ReviewSummary,
)
from backend.app.services.publisher import GitHubPublisher, format_review_markdown


class FakeGitHubAppClient:
    def __init__(self):
        self.payload = None

    def update_check_run(self, owner, repo, check_run_id, token, payload):
        self.payload = payload
        return {"id": check_run_id, "html_url": "https://github.com/team/repo/runs/1"}


def _review_result(route_name="policy_context_review"):
    route = ReviewRoute(
        name=route_name,
        model_tier="solar3-medium" if route_name == "policy_context_review" else "solar3-high",
        use_rag=True,
        focus=["repo_policy"],
        reasons=["checks passed or no failing check detected", "repository policy is available"],
        confidence=0.9,
    )
    return ReviewResult(
        review_run_id="run-1",
        status="completed",
        idempotency_key="key",
        summary=ReviewSummary(
            route_name=route.name,
            model_tier=route.model_tier,
            overall_risk="medium",
            short_comment="리뷰가 완료되었습니다.",
        ),
        findings=[],
        route=route,
        features=PullRequestFeatures(
            syntax_status="unknown",
            lint_status="unknown",
            test_status="passed",
            changed_files_count=1,
            changed_lines=1,
            risk_files=[],
            policy_available=True,
            router_confidence=0.9,
        ),
        model_call=ModelCallUsage(
            provider="upstage",
            model="solar-pro3",
            reasoning_effort="medium",
        ),
    )


class PublisherTest(unittest.TestCase):
    def test_review_markdown_hides_internal_tier_and_mentions_checks_button(self):
        markdown = format_review_markdown(_review_result())

        self.assertIn("- 리뷰 유형:", markdown)
        self.assertIn("GitHub Checks 화면", markdown)
        self.assertNotIn("Review tier", markdown)
        self.assertNotIn("Reasoning effort", markdown)
        self.assertNotIn("solar3-medium", markdown)

    def test_standard_review_check_run_includes_deep_review_action(self):
        app_client = FakeGitHubAppClient()
        publisher = GitHubPublisher(app_client=app_client)
        request = ReviewRequest(
            repository=RepositoryPayload(provider="github", owner="team", name="repo"),
            pull_request=PullRequestPayload(
                number=7,
                title="Test",
                author="dev",
                base_sha="base",
                head_sha="head",
                base_branch="main",
                head_branch="feature",
            ),
            github=GitHubPayload(installation_id="123", check_run_id="456"),
        )

        publisher._complete_check_run(request, _review_result(), "token")

        self.assertEqual(app_client.payload["status"], "completed")
        self.assertEqual(app_client.payload["conclusion"], "success")
        self.assertEqual(app_client.payload["actions"][0]["label"], "심층 리뷰 실행")
        self.assertEqual(app_client.payload["actions"][0]["identifier"], "run_deep_review")


if __name__ == "__main__":
    unittest.main()
