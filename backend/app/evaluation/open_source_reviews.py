from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

MAINTAINER_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


class GitHubPublicDataClient:
    def __init__(self, token: str | None = None, timeout: int = 30) -> None:
        self.token = token or os.getenv("GITHUB_TOKEN")
        self.timeout = timeout

    def get_json(self, path: str) -> Any:
        url = path if path.startswith("http") else f"https://api.github.com{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-code-review-agent-evaluation",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                    raise
                time.sleep(2**attempt)
        raise RuntimeError("GitHub request retry loop ended unexpectedly")

    def paginate(self, path: str, *, max_items: int | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 101):
            payload = self.get_json(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(payload, list) or not payload:
                break
            items.extend(item for item in payload if isinstance(item, dict))
            if max_items is not None and len(items) >= max_items:
                return items[:max_items]
            if len(payload) < 100:
                break
        return items


def _user(payload: dict[str, Any]) -> str:
    user = payload.get("user")
    return str(user.get("login") or "") if isinstance(user, dict) else ""


def _is_bot(login: str) -> bool:
    return login.endswith("[bot]") or login.endswith("-bot")


def _is_maintainer(payload: dict[str, Any]) -> bool:
    return str(payload.get("author_association") or "").upper() in MAINTAINER_ASSOCIATIONS


def normalize_review(payload: dict[str, Any]) -> dict[str, Any]:
    reviewer = _user(payload)
    return {
        "id": payload.get("id"),
        "state": str(payload.get("state") or "").upper(),
        "commit_id": str(payload.get("commit_id") or ""),
        "submitted_at": payload.get("submitted_at"),
        "reviewer": reviewer,
        "author_association": payload.get("author_association"),
        "is_bot": _is_bot(reviewer),
        "is_maintainer": _is_maintainer(payload),
        "body": str(payload.get("body") or ""),
        "html_url": payload.get("html_url"),
    }


def normalize_review_comment(payload: dict[str, Any]) -> dict[str, Any]:
    author = _user(payload)
    return {
        "id": payload.get("id"),
        "review_id": payload.get("pull_request_review_id"),
        "parent_id": payload.get("in_reply_to_id"),
        "author": author,
        "author_association": payload.get("author_association"),
        "is_bot": _is_bot(author),
        "is_maintainer": _is_maintainer(payload),
        "path": str(payload.get("path") or ""),
        "line": payload.get("line") or payload.get("original_line"),
        "side": payload.get("side"),
        "commit_id": str(payload.get("commit_id") or ""),
        "original_commit_id": str(payload.get("original_commit_id") or ""),
        "diff_hunk": str(payload.get("diff_hunk") or ""),
        "body": str(payload.get("body") or ""),
        "created_at": payload.get("created_at"),
        "html_url": payload.get("html_url"),
    }


def collect_repository_reviews(
    repository: str,
    *,
    max_prs: int = 25,
    state: str = "closed",
    client: GitHubPublicDataClient | None = None,
) -> list[dict[str, Any]]:
    owner, separator, name = repository.partition("/")
    if not separator or not owner or not name:
        raise ValueError("repository must use owner/name format")
    if state not in {"open", "closed", "all"}:
        raise ValueError("state must be open, closed, or all")
    github = client or GitHubPublicDataClient()
    pulls = github.paginate(
        f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}/pulls"
        f"?state={state}&sort=updated&direction=desc",
        max_items=max_prs,
    )
    records: list[dict[str, Any]] = []
    for pull_summary in pulls:
        number = int(pull_summary.get("number") or 0)
        if number <= 0:
            continue
        detail = github.get_json(f"/repos/{owner}/{name}/pulls/{number}")
        reviews = github.paginate(f"/repos/{owner}/{name}/pulls/{number}/reviews")
        comments = github.paginate(f"/repos/{owner}/{name}/pulls/{number}/comments")
        normalized_reviews = [normalize_review(review) for review in reviews]
        normalized_comments = [normalize_review_comment(comment) for comment in comments]
        records.append(
            {
                "schema_version": 1,
                "repository": repository,
                "pull_number": number,
                "title": str(detail.get("title") or ""),
                "state": str(detail.get("state") or ""),
                "merged": bool(detail.get("merged")),
                "draft": bool(detail.get("draft")),
                "created_at": detail.get("created_at"),
                "closed_at": detail.get("closed_at"),
                "merged_at": detail.get("merged_at"),
                "base_sha": str((detail.get("base") or {}).get("sha") or ""),
                "head_sha": str((detail.get("head") or {}).get("sha") or ""),
                "additions": int(detail.get("additions") or 0),
                "deletions": int(detail.get("deletions") or 0),
                "changed_files": int(detail.get("changed_files") or 0),
                "reviews": normalized_reviews,
                "review_comments": normalized_comments,
                "candidate_review_commits": sorted(
                    {
                        review["commit_id"]
                        for review in normalized_reviews
                        if review["is_maintainer"]
                        and review["commit_id"]
                        and review["state"] in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED"}
                    }
                ),
            }
        )
    return records


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    reviews = [review for record in records for review in record.get("reviews", [])]
    comments = [
        comment for record in records for comment in record.get("review_comments", [])
    ]
    maintainer_roots = [
        comment
        for comment in comments
        if comment.get("is_maintainer") and not comment.get("parent_id")
    ]
    return {
        "pull_requests": len(records),
        "merged_pull_requests": sum(bool(record.get("merged")) for record in records),
        "reviews": len(reviews),
        "review_states": dict(Counter(str(review.get("state")) for review in reviews)),
        "inline_comments": len(comments),
        "maintainer_root_comments": len(maintainer_roots),
        "bot_comments": sum(bool(comment.get("is_bot")) for comment in comments),
        "human_replies": sum(
            bool(comment.get("parent_id")) and not bool(comment.get("is_bot"))
            for comment in comments
        ),
    }


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
