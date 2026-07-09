from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Protocol

from backend.app.core.config import Settings
from backend.app.core.schemas import ReviewRequest, ReviewResult
from backend.app.services.github_app import DEEP_REVIEW_ACTION_IDENTIFIER, GitHubAppClient


class ReviewPublisher(Protocol):
    def publish(self, request: ReviewRequest, result: ReviewResult) -> dict[str, object]:
        ...


REVIEW_TYPE_LABELS = {
    "simple_failure_review": "실패 원인 빠른 리뷰",
    "policy_context_review": "정책 기반 표준 리뷰",
    "deep_quality_review": "심층 품질 리뷰",
}

ROUTE_REASON_LABELS = {
    "syntax, lint, or test failed": "문법, 린트 또는 테스트 실패가 감지됨",
    "manual deep review requested": "사용자가 심층 리뷰를 직접 요청함",
    "checks passed or no failing check detected": "실패한 체크가 없음",
    "repository policy is available": "저장소 정책 컨텍스트 사용 가능",
    "repository policy is unavailable; falling back to general review": (
        "저장소 정책이 없어 일반 리뷰로 진행"
    ),
    "high-risk signals detected; deep review can be requested": (
        "고위험 변경 신호 감지, 필요 시 심층 리뷰 선택 가능"
    ),
    "large diff detected; deep review can be requested": (
        "큰 변경 규모 감지, 필요 시 심층 리뷰 선택 가능"
    ),
    "many changed files detected; deep review can be requested": (
        "많은 파일 변경 감지, 필요 시 심층 리뷰 선택 가능"
    ),
}


def _review_type_label(result: ReviewResult) -> str:
    return REVIEW_TYPE_LABELS.get(result.route.name, "자동 코드 리뷰")


def _review_context_label(result: ReviewResult) -> str:
    if result.route.use_rag:
        return "저장소 정책/RAG 참조"
    return "체크 결과와 변경 diff 기반"


def _route_reason_summary(result: ReviewResult) -> str:
    if not result.route.reasons:
        return "자동 라우팅 기준"
    return ", ".join(ROUTE_REASON_LABELS.get(reason, reason) for reason in result.route.reasons)


def _supports_manual_deep_review(result: ReviewResult) -> bool:
    return result.route.name == "policy_context_review"


def format_review_markdown(result: ReviewResult) -> str:
    lines = [
        "## AI Code Review",
        "",
        f"- 리뷰 유형: `{_review_type_label(result)}`",
        f"- 선택 사유: {_route_reason_summary(result)}",
        f"- 처리 방식: `{_review_context_label(result)}`",
        f"- 사용 모델: `{result.model_call.model}`",
        f"- 위험도: `{result.summary.overall_risk}`",
        f"- 요약: {result.summary.short_comment}",
        "",
        "### 리뷰 결과",
    ]
    if _supports_manual_deep_review(result):
        lines.extend(
            [
                "",
                "> 다른 시각의 심층 리뷰가 필요하면 GitHub Checks 화면의 "
                "`심층 리뷰 실행` 버튼으로 추가 실행할 수 있습니다.",
            ]
        )
    if not result.findings:
        lines.append("")
        lines.append("생성된 리뷰 결과가 없습니다.")
    for index, finding in enumerate(result.findings, start=1):
        location = finding.file_path
        if finding.line_start:
            location = f"{location}:{finding.line_start}"
        lines.extend(
            [
                "",
                f"{index}. **{finding.severity} / {finding.category}** - `{location}`",
                f"   - {finding.message}",
                f"   - 개선 제안: {finding.suggestion}",
            ]
        )
        if finding.policy_source:
            lines.append(f"   - 참고 정책: `{finding.policy_source}`")
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
        body = self._post_issue_comment(request, token, format_review_markdown(result))
        check_run = self._complete_check_run(request, result, token)
        mode = "github_app" if self.app_client and not self.token else "github"
        return {
            "mode": mode,
            "comment_id": body.get("id"),
            "html_url": body.get("html_url"),
            "check_run_id": check_run.get("id") if check_run else None,
            "check_run_url": check_run.get("html_url") if check_run else None,
        }

    def _post_issue_comment(
        self,
        request: ReviewRequest,
        token: str,
        markdown: str,
    ) -> dict[str, object]:
        url = (
            "https://api.github.com/repos/"
            f"{request.repository.owner}/{request.repository.name}/issues/"
            f"{request.pull_request.number}/comments"
        )
        payload = json.dumps({"body": markdown}).encode("utf-8")
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
            response_body = response.read().decode("utf-8")
        return json.loads(response_body) if response_body else {}

    def _complete_check_run(
        self,
        request: ReviewRequest,
        result: ReviewResult,
        token: str,
    ) -> dict[str, object]:
        if not self.app_client or not request.github.check_run_id:
            return {}
        summary = (
            f"{result.summary.short_comment}\n\n"
            f"- 리뷰 유형: {_review_type_label(result)}\n"
            f"- 선택 사유: {_route_reason_summary(result)}\n"
            f"- 리뷰 결과: {len(result.findings)}"
        )
        payload: dict[str, object] = {
            "status": "completed",
            "conclusion": "success",
            "completed_at": _utc_now_iso(),
            "output": {
                "title": "AI Code Review completed",
                "summary": summary,
            },
        }
        if _supports_manual_deep_review(result):
            payload["actions"] = [
                {
                    "label": "심층 리뷰 실행",
                    "description": "다른 시각의 심층 리뷰를 실행합니다.",
                    "identifier": DEEP_REVIEW_ACTION_IDENTIFIER,
                }
            ]
        return self.app_client.update_check_run(
            request.repository.owner,
            request.repository.name,
            request.github.check_run_id,
            token,
            payload,
        )

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


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
