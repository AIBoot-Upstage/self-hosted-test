import hashlib
import hmac
import unittest

from backend.app.core.config import Settings
from backend.app.services.github_app import (
    GitHubWebhookError,
    GitHubWebhookProcessor,
    verify_github_signature,
)


class FakeGitHubClient:
    def installation_token(self, installation_id):
        self.installation_id = str(installation_id)
        return "installation-token"

    def get_pull_request(self, owner, repo, pull_number, token):
        return _pull_request_payload(number=pull_number)

    def list_pull_files(self, owner, repo, pull_number, token):
        return [
            {
                "filename": "app/api/items.py",
                "status": "modified",
                "additions": 12,
                "deletions": 2,
                "patch": "+return items",
            }
        ]

    def list_check_runs(self, owner, repo, ref, token):
        return [
            {
                "name": "test",
                "status": "completed",
                "conclusion": "success",
                "output": {"summary": "12 passed"},
                "html_url": "https://github.com/team/repo/actions/runs/1",
            }
        ]


def _signature(payload_body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _repository_payload():
    return {
        "name": "repo",
        "full_name": "team/repo",
        "default_branch": "main",
        "owner": {"login": "team"},
    }


def _pull_request_payload(number=7):
    return {
        "number": number,
        "title": "Add API endpoint",
        "draft": False,
        "user": {"login": "dev"},
        "base": {"sha": "base-sha", "ref": "main"},
        "head": {"sha": "head-sha", "ref": "feature/items"},
    }


class GitHubWebhookTest(unittest.TestCase):
    def test_verify_github_signature_accepts_valid_hmac(self):
        body = b'{"zen":"Keep it logically awesome."}'
        verify_github_signature(body, "secret", _signature(body, "secret"))

    def test_verify_github_signature_rejects_invalid_hmac(self):
        with self.assertRaises(GitHubWebhookError):
            verify_github_signature(b"{}", "secret", "sha256=bad")

    def test_pull_request_waits_for_checks_in_after_checks_mode(self):
        settings = Settings(github_webhook_review_mode="after_checks")
        processor = GitHubWebhookProcessor(settings, client=FakeGitHubClient())

        plan = processor.review_plan(
            "pull_request",
            "delivery-1",
            {
                "action": "opened",
                "repository": _repository_payload(),
                "installation": {"id": 123},
                "pull_request": _pull_request_payload(),
            },
        )

        self.assertEqual(plan.status, "accepted")
        self.assertFalse(plan.requests)

    def test_check_suite_completed_builds_review_request(self):
        settings = Settings(github_webhook_review_mode="after_checks")
        processor = GitHubWebhookProcessor(settings, client=FakeGitHubClient())

        plan = processor.review_plan(
            "check_suite",
            "delivery-2",
            {
                "action": "completed",
                "repository": _repository_payload(),
                "installation": {"id": 123},
                "check_suite": {"pull_requests": [{"number": 7}]},
            },
        )

        self.assertEqual(plan.status, "ready")
        self.assertEqual(len(plan.requests), 1)
        request = plan.requests[0]
        self.assertEqual(request.repository.full_name, "team/repo")
        self.assertEqual(request.pull_request.number, 7)
        self.assertEqual(request.github.delivery_id, "delivery-2")
        self.assertEqual(request.github.installation_id, "123")
        self.assertEqual(request.checks[0].kind, "test")
        self.assertEqual(request.changed_files[0].path, "app/api/items.py")

    def test_check_run_waits_for_check_suite_in_after_checks_mode(self):
        settings = Settings(github_webhook_review_mode="after_checks")
        processor = GitHubWebhookProcessor(settings, client=FakeGitHubClient())

        plan = processor.review_plan(
            "check_run",
            "delivery-3",
            {
                "action": "completed",
                "repository": _repository_payload(),
                "installation": {"id": 123},
                "check_run": {
                    "name": "test",
                    "pull_requests": [{"number": 7}],
                    "app": {"id": 999},
                },
            },
        )

        self.assertEqual(plan.status, "accepted")
        self.assertFalse(plan.requests)


if __name__ == "__main__":
    unittest.main()
