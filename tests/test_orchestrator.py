import tempfile
import unittest
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.core.schemas import ReviewRequest
from backend.app.services.orchestrator import create_orchestrator


class OrchestratorTest(unittest.TestCase):
    def test_orchestrator_runs_local_review(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            policy_root = tmp_path / "policies"
            data_dir = tmp_path / "data"
            policy_root.mkdir()
            (policy_root / "review.md").write_text(
                "# Test Policy\n\nAPI changes should include tests.\n",
                encoding="utf-8",
            )
            settings = Settings(
                policy_root=policy_root,
                local_data_dir=data_dir,
                review_store_path=data_dir / "reviews.json",
                comment_output_dir=data_dir / "comments",
                llm_mode="mock",
                publish_mode="local",
            )
            request = ReviewRequest.from_dict(
                {
                    "repository": {"owner": "team", "name": "repo"},
                    "pull_request": {
                        "number": 7,
                        "title": "Add API endpoint",
                        "author": "dev",
                        "base_sha": "a",
                        "head_sha": "b",
                        "base_branch": "main",
                        "head_branch": "feature",
                    },
                    "checks": [
                        {
                            "kind": "test",
                            "status": "completed",
                            "conclusion": "success",
                            "summary": "",
                        }
                    ],
                    "changed_files": [
                        {"path": "app/api/items.py", "additions": 12, "deletions": 0, "patch": ""}
                    ],
                }
            )

            result = create_orchestrator(settings).run_review(request)

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.route.name, "policy_context_review")
            self.assertTrue(result.findings)
            self.assertTrue(settings.review_store_path.exists())
            self.assertTrue(list(settings.comment_output_dir.glob("*.md")))


if __name__ == "__main__":
    unittest.main()
