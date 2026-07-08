from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _github_request(path: str) -> Any:
    token = _env("GITHUB_TOKEN")
    repository = _env("GITHUB_REPOSITORY")
    if not token or not repository:
        raise RuntimeError("GITHUB_TOKEN and GITHUB_REPOSITORY are required")
    url = f"https://api.github.com/repos/{repository}{path}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _pull_files(pr_number: int) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"per_page": 100, "page": page})
        payload = _github_request(f"/pulls/{pr_number}/files?{query}")
        if not payload:
            break
        files.extend(payload)
        if len(payload) < 100:
            break
        page += 1
    return [
        {
            "path": item.get("filename", ""),
            "status": item.get("status", "modified"),
            "additions": item.get("additions", 0),
            "deletions": item.get("deletions", 0),
            "patch": item.get("patch", ""),
        }
        for item in files
    ]


def _lint_check() -> dict[str, str]:
    payload = _read_json(Path("lint-result.json"))
    if payload is None:
        return {"kind": "lint", "status": "skipped", "conclusion": "skipped", "summary": ""}
    failed_count = len(payload) if isinstance(payload, list) else 0
    conclusion = "success" if failed_count == 0 else "failure"
    return {
        "kind": "lint",
        "status": "completed",
        "conclusion": conclusion,
        "summary": f"ruff findings: {failed_count}",
    }


def _test_check() -> dict[str, str]:
    payload = _read_json(Path("test-result.json"))
    if payload is None:
        return {"kind": "test", "status": "skipped", "conclusion": "skipped", "summary": ""}
    exitcode = int(payload.get("exitcode", 1)) if isinstance(payload, dict) else 1
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    conclusion = "success" if exitcode == 0 else "failure"
    return {
        "kind": "test",
        "status": "completed",
        "conclusion": conclusion,
        "summary": json.dumps(summary, ensure_ascii=False),
    }


def _post_review_request(payload: dict[str, Any]) -> Any:
    api_url = _env("AI_REVIEWER_API_URL").rstrip("/")
    token = _env("AI_REVIEWER_TOKEN")
    if not api_url:
        raise RuntimeError("AI_REVIEWER_API_URL is required")
    request = urllib.request.Request(
        f"{api_url}/v1/reviews",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    event_path = Path(_env("GITHUB_EVENT_PATH"))
    event = _read_json(event_path)
    if not event or "pull_request" not in event:
        raise RuntimeError("This script must run from a pull_request event")

    repository_name = event["repository"]["name"]
    owner = event["repository"]["owner"]["login"]
    pr = event["pull_request"]
    payload = {
        "repository": {
            "provider": "github",
            "owner": owner,
            "name": repository_name,
            "default_branch": event["repository"].get("default_branch", "main"),
        },
        "pull_request": {
            "number": pr["number"],
            "title": pr.get("title", ""),
            "author": pr.get("user", {}).get("login", ""),
            "base_sha": pr.get("base", {}).get("sha", ""),
            "head_sha": pr.get("head", {}).get("sha", ""),
            "base_branch": pr.get("base", {}).get("ref", ""),
            "head_branch": pr.get("head", {}).get("ref", ""),
        },
        "checks": [_lint_check(), _test_check()],
        "changed_files": _pull_files(pr["number"]),
        "github": {
            "run_id": _env("GITHUB_RUN_ID"),
            "event_name": _env("GITHUB_EVENT_NAME", "pull_request"),
        },
    }
    result = _post_review_request(payload)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"AI review request failed: {exc}", file=sys.stderr)
        raise

