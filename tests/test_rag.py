import tempfile
import unittest
from pathlib import Path

from backend.app.core.schemas import ReviewRequest
from backend.app.services.rag import LocalPolicyIndex


class LocalPolicyIndexTest(unittest.TestCase):
    def test_local_policy_index_retrieves_relevant_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            (tmp_path / "api-policy.md").write_text(
                "# API Contract\n\nProfile API responses should include code and message fields.\n",
                encoding="utf-8",
            )
            request = ReviewRequest.from_dict(
                {
                    "repository": {"owner": "team", "name": "repo"},
                    "pull_request": {
                        "number": 1,
                        "title": "Add profile API response",
                        "author": "dev",
                        "base_sha": "a",
                        "head_sha": "b",
                        "base_branch": "main",
                        "head_branch": "feature",
                    },
                    "changed_files": [
                        {
                            "path": "app/api/profile.py",
                            "additions": 5,
                            "deletions": 1,
                            "patch": "+return {'data': profile}",
                        }
                    ],
                }
            )

            index = LocalPolicyIndex(tmp_path)
            results = index.search(request)

            self.assertTrue(index.has_policy())
            self.assertTrue(results)
            self.assertEqual(results[0].source_path, "api-policy.md")


if __name__ == "__main__":
    unittest.main()
