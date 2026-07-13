import json
import unittest
from unittest.mock import patch

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
    ReviewFinding,
)
from backend.app.services.publisher import GitHubPublisher, format_review_markdown


class FakeGitHubAppClient:
    def __init__(self):
        self.payload = None

    def update_check_run(self, owner, repo, check_run_id, token, payload):
        self.payload = payload
        return {"id": check_run_id, "html_url": "https://github.com/team/repo/runs/1"}


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return b'{"id": 77}'


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

    def test_summary_mentions_inline_findings_without_repeating_them(self):
        result = _review_result()
        inline_finding = ReviewFinding(
            severity="high",
            category="functional_correctness",
            file_path="app/service.py",
            line_start=10,
            line_end=10,
            message="Empty input raises an exception.",
            suggestion="Return an empty result before indexing.",
        )
        result = ReviewResult(
            **{
                **result.__dict__,
                "findings": [inline_finding],
            }
        )

        markdown = format_review_markdown(result, findings=[], inline_findings_count=1)

        self.assertIn("1개 항목은 diff inline comment", markdown)
        self.assertNotIn("Empty input raises", markdown)

    def test_posts_validated_findings_as_pull_request_review_comments(self):
        publisher = GitHubPublisher(token="token")
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
        )
        finding = ReviewFinding(
            severity="high",
            category="functional_correctness",
            file_path="app/service.py",
            line_start=10,
            line_end=10,
            message="Empty input raises an exception.",
            suggestion="Return an empty result before indexing.",
        )

        with patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            response = publisher._post_pull_review(request, "token", [finding])

        http_request = urlopen.call_args.args[0]
        payload = json.loads(http_request.data)
        self.assertEqual(response["id"], 77)
        self.assertEqual(payload["commit_id"], "head")
        self.assertEqual(payload["comments"][0]["path"], "app/service.py")
        self.assertEqual(payload["comments"][0]["line"], 10)
        self.assertEqual(payload["comments"][0]["side"], "RIGHT")


if __name__ == "__main__":
    unittest.main()
