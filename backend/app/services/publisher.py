from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Protocol

from backend.app.core.config import Settings
from backend.app.core.schemas import ReviewRequest, ReviewResult
from backend.app.services.github_app import GitHubAppClient


class ReviewPublisher(Protocol):
    def publish(self, request: ReviewRequest, result: ReviewResult) -> dict[str, object]:
        ...


def format_review_markdown(result: ReviewResult) -> str:
    lines = [
        "## AI Code Review",
        "",
        f"- Route: `{result.route.name}`",
        f"- Review tier: `{result.route.model_tier}`",
        f"- Model: `{result.model_call.model}`",
        f"- Reasoning effort: `{result.model_call.reasoning_effort or 'default'}`",
        f"- Risk: `{result.summary.overall_risk}`",
        f"- Summary: {result.summary.short_comment}",
        "",
        "### Findings",
    ]
    if not result.findings:
        lines.append("")
        lines.append("No findings were generated.")
    for index, finding in enumerate(result.findings, start=1):
        location = finding.file_path
        if finding.line_start:
            location = f"{location}:{finding.line_start}"
        lines.extend(
            [
                "",
                f"{index}. **{finding.severity} / {finding.category}** - `{location}`",
                f"   - {finding.message}",
                f"   - Suggestion: {finding.suggestion}",
            ]
        )
        if finding.policy_source:
            lines.append(f"   - Policy: `{finding.policy_source}`")
    return "\n".join(lines).strip() + "\n"


class LocalPublisher:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def publish(self, request: ReviewRequest, result: ReviewResult) -> dict[str, object]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{result.review_run_id}.md"
        path.write_text(format_review_markdown(result), encoding="utf-8")
        return {"mode": "local", "path": str(path)}


class GitHubPublisher:
    def __init__(
        self,
        token: str | None = None,
        app_client: GitHubAppClient | None = None,
    ) -> None:
        self.token = token
        self.app_client = app_client

    def publish(self, request: ReviewRequest, result: ReviewResult) -> dict[str, object]:
        token = self._token_for(request)
        url = (
            "https://api.github.com/repos/"
            f"{request.repository.owner}/{request.repository.name}/issues/"
            f"{request.pull_request.number}/comments"
        )
        payload = json.dumps({"body": format_review_markdown(result)}).encode("utf-8")
        http_request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(http_request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
        mode = "github_app" if self.app_client and not self.token else "github"
        return {"mode": mode, "comment_id": body.get("id"), "html_url": body.get("html_url")}

    def _token_for(self, request: ReviewRequest) -> str:
        if self.token:
            return self.token
        if not self.app_client:
            raise RuntimeError("GitHub publisher requires GITHUB_TOKEN or GitHub App settings")
        if not request.github.installation_id:
            raise RuntimeError("GitHub App publish requires github.installation_id")
        return self.app_client.installation_token(request.github.installation_id)


def create_publisher(settings: Settings) -> ReviewPublisher:
    if settings.publish_mode == "github_app":
        return GitHubPublisher(app_client=GitHubAppClient(settings))
    if settings.publish_mode == "github" and settings.github_token:
        return GitHubPublisher(settings.github_token)
    return LocalPublisher(settings.comment_output_dir)
