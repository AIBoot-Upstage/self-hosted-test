from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Protocol

from backend.app.core.config import Settings
from backend.app.core.schemas import (
    ChangedFilePayload,
    FileChangeSummary,
    ModelCallUsage,
    PolicyChunk,
    ReviewFinding,
    ReviewRequest,
    ReviewRoute,
    ReviewSummary,
)


class LLMClient(Protocol):
    def generate_review(
        self,
        request: ReviewRequest,
        route: ReviewRoute,
        policies: list[PolicyChunk],
        messages: list[dict[str, str]],
        review_run_id: str | None = None,
        batch_index: int = 1,
        batch_count: int = 1,
    ) -> tuple[ReviewSummary, list[ReviewFinding], ModelCallUsage]:
        ...


def _first_changed_path(request: ReviewRequest) -> str:
    if request.changed_files:
        return request.changed_files[0].path
    return "unknown"


def _line_for_first_file(request: ReviewRequest) -> int | None:
    if not request.changed_files:
        return None
    return 1


def _fallback_file_change_summary(changed_file: ChangedFilePayload) -> str:
    status_labels = {
        "added": "새 파일 추가",
        "removed": "파일 삭제",
        "renamed": "파일 이름 변경",
        "modified": "파일 수정",
    }
    status = status_labels.get(changed_file.status.lower(), changed_file.status or "파일 수정")
    return f"{status}: {changed_file.additions}줄 추가, {changed_file.deletions}줄 삭제"


KOREAN_PATTERN = re.compile(r"[가-힣]")


def _korean_text(value: Any) -> str:
    text = str(value or "").strip()
    return text if KOREAN_PATTERN.search(text) else ""


def _file_summaries_from_payload(
    payload: Any,
    request: ReviewRequest,
) -> list[FileChangeSummary]:
    allowed_files = {changed_file.path: changed_file for changed_file in request.changed_files}
    parsed: dict[str, str] = {}
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            file_path = str(item.get("file_path") or "").strip()
            change_summary = _korean_text(
                item.get("change_summary") or item.get("summary") or ""
            )
            if file_path in allowed_files and change_summary and file_path not in parsed:
                parsed[file_path] = change_summary

    return [
        FileChangeSummary(
            file_path=changed_file.path,
            change_summary=parsed.get(
                changed_file.path,
                _fallback_file_change_summary(changed_file),
            ),
        )
        for changed_file in request.changed_files
    ]


def _fallback_change_summary(request: ReviewRequest) -> str:
    additions = sum(changed_file.additions for changed_file in request.changed_files)
    deletions = sum(changed_file.deletions for changed_file in request.changed_files)
    return (
        f"변경 파일 {len(request.changed_files)}개에서 "
        f"{additions}줄을 추가하고 {deletions}줄을 삭제했습니다."
    )


def _harness_card_contract(messages: list[dict[str, str]]) -> tuple[bool, str | None]:
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        try:
            payload = json.loads(message.get("content") or "{}")
        except json.JSONDecodeError:
            continue
        harness = payload.get("review_harness")
        if not isinstance(harness, dict):
            continue
        cards = harness.get("knowledge_cards") or []
        if cards and isinstance(cards[0], dict):
            return True, str(cards[0].get("card_id") or "") or None
        return True, None
    return False, None


class MockLLMClient:
    """Deterministic local reviewer used for development and tests."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def generate_review(
        self,
        request: ReviewRequest,
        route: ReviewRoute,
        policies: list[PolicyChunk],
        messages: list[dict[str, str]],
        review_run_id: str | None = None,
        batch_index: int = 1,
        batch_count: int = 1,
    ) -> tuple[ReviewSummary, list[ReviewFinding], ModelCallUsage]:
        start = time.perf_counter()
        findings: list[ReviewFinding] = []
        file_path = _first_changed_path(request)
        line = _line_for_first_file(request)
        harness_contract, knowledge_card_id = _harness_card_contract(messages)

        if route.name == "simple_failure_review":
            failed_checks = [check for check in request.checks if check.is_failed]
            summary_text = "하나 이상의 체크가 실패했습니다. 병합 전에 실패 로그를 확인해야 합니다."
            if failed_checks:
                summary_text = (
                    f"{failed_checks[0].kind} 체크가 실패했습니다: "
                    f"{failed_checks[0].summary[:160]}"
                )
            findings.append(
                ReviewFinding(
                    severity="high",
                    category="failure",
                    file_path=file_path,
                    line_start=line,
                    line_end=line,
                    message="PR 체크가 실패해 현재 변경을 정상적으로 검증할 수 없습니다.",
                    suggestion="실패한 체크 로그를 열고 오류와 연결된 변경 파일부터 수정합니다.",
                    evidence={"failed_checks": [check.kind for check in failed_checks]},
                    knowledge_card_id=knowledge_card_id,
                    confidence=0.88,
                )
            )
            risk = "high"
        elif route.name == "deep_quality_review":
            risk = "high"
            findings.append(
                ReviewFinding(
                    severity="high",
                    category="architecture",
                    file_path=file_path,
                    line_start=line,
                    line_end=line,
                    message="변경 범위가 크거나 운영 영향이 있는 코드 경로를 수정했습니다.",
                    suggestion=(
                        "권한, 데이터 일관성, 롤백 동작과 운영 관측성을 중심으로 "
                        "추가 사람 리뷰를 수행합니다."
                    ),
                    evidence={"route_reasons": route.reasons},
                    policy_source=policies[0].source_path if policies else None,
                    knowledge_card_id=knowledge_card_id,
                    confidence=0.78,
                )
            )
        else:
            risk = "medium" if policies else "low"
            if policies:
                policy = policies[0]
                findings.append(
                    ReviewFinding(
                        severity="medium",
                        category=policy.policy_type,
                        file_path=file_path,
                        line_start=line,
                        line_end=line,
                        message="저장소 정책과 직접 관련된 변경이 포함되어 있습니다.",
                        suggestion=(
                            "변경 코드를 참고 정책과 비교하고 불일치하는 테스트나 API 동작을 "
                            "정책에 맞게 수정합니다."
                        ),
                        evidence={
                            "section_title": policy.section_title,
                            "policy_score": policy.score,
                        },
                        policy_source=f"{policy.source_path}#{policy.section_title}",
                        knowledge_card_id=knowledge_card_id,
                        confidence=0.74,
                    )
                )
            else:
                findings.append(
                    ReviewFinding(
                        severity="low",
                        category="style",
                        file_path=file_path,
                        line_start=line,
                        line_end=line,
                        message="저장소 정책이 없어 일반 변경 정보만 검토했습니다.",
                        suggestion="구체적인 기준이 필요하면 POLICY_ROOT에 저장소 정책을 추가합니다.",
                        knowledge_card_id=knowledge_card_id,
                        confidence=0.62,
                    )
                )

        if harness_contract and not knowledge_card_id:
            findings = []

        model = self.settings.model_for_tier(route.model_tier)
        reasoning_effort = self.settings.reasoning_effort_for_tier(route.model_tier)
        latency_ms = int((time.perf_counter() - start) * 1000)
        usage = ModelCallUsage(
            provider="mock",
            model=model,
            prompt_tokens=sum(len(message["content"]) for message in messages) // 4,
            completion_tokens=250,
            latency_ms=latency_ms,
            reasoning_effort=reasoning_effort,
        )
        summary = ReviewSummary(
            route_name=route.name,
            model_tier=route.model_tier,
            overall_risk=risk,
            short_comment=(
                summary_text
                if route.name == "simple_failure_review"
                else f"변경 파일 {len(request.changed_files)}개를 검토했습니다."
            ),
            change_summary=_fallback_change_summary(request),
            file_summaries=_file_summaries_from_payload([], request),
        )
        return summary, findings, usage


class LiteLLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._langfuse_ready = False
        self._langfuse_lock = threading.Lock()

    def _ensure_langfuse_callback(self) -> None:
        if (
            self._langfuse_ready
            or not self.settings.langfuse_public_key
            or not self.settings.langfuse_secret_key
        ):
            return
        with self._langfuse_lock:
            if self._langfuse_ready:
                return
            import litellm

            os.environ["LANGFUSE_PUBLIC_KEY"] = self.settings.langfuse_public_key
            os.environ["LANGFUSE_SECRET_KEY"] = self.settings.langfuse_secret_key
            os.environ["LANGFUSE_HOST"] = self.settings.langfuse_host
            if "langfuse" not in litellm.success_callback:
                litellm.success_callback.append("langfuse")
            if "langfuse" not in litellm.failure_callback:
                litellm.failure_callback.append("langfuse")
            self._langfuse_ready = True

    def generate_review(
        self,
        request: ReviewRequest,
        route: ReviewRoute,
        policies: list[PolicyChunk],
        messages: list[dict[str, str]],
        review_run_id: str | None = None,
        batch_index: int = 1,
        batch_count: int = 1,
    ) -> tuple[ReviewSummary, list[ReviewFinding], ModelCallUsage]:
        try:
            from litellm import completion
        except ModuleNotFoundError as exc:
            raise RuntimeError("litellm is not installed. Run `pip install -e .`.") from exc

        self._ensure_langfuse_callback()

        start = time.perf_counter()
        model = self.settings.model_for_tier(route.model_tier)
        litellm_model = _litellm_model_id(model, self.settings.upstage_api_base_url)
        reasoning_effort = self.settings.reasoning_effort_for_tier(route.model_tier)
        max_tokens = self.settings.max_tokens_for_tier(route.model_tier)
        completion_kwargs: dict[str, Any] = {
            "model": litellm_model,
            "messages": messages,
            "api_key": self.settings.upstage_api_key,
            "api_base": self.settings.upstage_api_base_url,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "timeout": 90,
            "metadata": {
                "review_run_id": review_run_id,
                "route_name": route.name,
                "model_tier": route.model_tier,
                "repository": request.repository.full_name,
                "pull_request_number": request.pull_request.number,
                "batch_index": batch_index,
                "batch_count": batch_count,
                "max_tokens": max_tokens,
            },
        }
        if reasoning_effort:
            completion_kwargs["reasoning_effort"] = reasoning_effort
            completion_kwargs["allowed_openai_params"] = ["reasoning_effort"]
        response = completion(**completion_kwargs)
        latency_ms = int((time.perf_counter() - start) * 1000)
        content = response.choices[0].message.content
        if not content or not content.strip():
            finish_reason = getattr(response.choices[0], "finish_reason", "unknown")
            usage_payload = getattr(response, "usage", None)
            completion_tokens = int(getattr(usage_payload, "completion_tokens", 0) or 0)
            raise RuntimeError(
                "LLM response content was empty "
                f"(finish_reason={finish_reason}, completion_tokens={completion_tokens})"
            )
        parsed = _parse_json(content)

        summary_payload = parsed.get("summary", {})
        if not isinstance(summary_payload, dict):
            summary_payload = {}
        findings_payload = parsed.get("findings", [])
        file_summaries = _file_summaries_from_payload(
            summary_payload.get("file_summaries"),
            request,
        )
        change_summary = _korean_text(summary_payload.get("change_summary"))
        if not change_summary:
            change_summary = _korean_text(summary_payload.get("short_comment"))
        if not change_summary:
            change_summary = _fallback_change_summary(request)
        short_comment = _korean_text(summary_payload.get("short_comment")) or change_summary
        summary = ReviewSummary(
            route_name=route.name,
            model_tier=route.model_tier,
            overall_risk=str(summary_payload.get("overall_risk", "medium")),
            short_comment=short_comment,
            change_summary=change_summary,
            file_summaries=file_summaries,
        )
        findings = [
            _finding_from_payload(item)
            for item in findings_payload
            if isinstance(item, dict)
        ]
        usage_payload = getattr(response, "usage", None)
        usage = ModelCallUsage(
            provider="upstage",
            model=model,
            prompt_tokens=int(getattr(usage_payload, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage_payload, "completion_tokens", 0) or 0),
            latency_ms=latency_ms,
            reasoning_effort=reasoning_effort,
            batch_count=1,
        )
        return summary, findings, usage


def _litellm_model_id(model: str, api_base: str | None) -> str:
    if "/" in model:
        return model
    if api_base:
        return f"openai/{model}"
    return model


def _parse_json(content: str | None) -> dict[str, Any]:
    if not content or not content.strip():
        raise RuntimeError("LLM response did not contain JSON content")

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError("LLM response did not contain a JSON object") from exc
        try:
            parsed = json.loads(content[start : end + 1])
        except json.JSONDecodeError as nested_exc:
            raise RuntimeError("LLM response contained an invalid JSON object") from nested_exc

    if not isinstance(parsed, dict):
        raise RuntimeError("LLM response JSON must be an object")
    return parsed


def _finding_from_payload(payload: dict[str, Any]) -> ReviewFinding:
    def _maybe_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return ReviewFinding(
        severity=str(payload.get("severity", "medium")),
        category=str(payload.get("category", "general")),
        file_path=str(payload.get("file_path", "unknown")),
        line_start=_maybe_int(payload.get("line_start")),
        line_end=_maybe_int(payload.get("line_end")),
        message=str(payload.get("message", "")),
        suggestion=str(payload.get("suggestion", "")),
        evidence=payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {},
        policy_source=payload.get("policy_source"),
        knowledge_card_id=payload.get("knowledge_card_id"),
        confidence=float(payload.get("confidence", 0.7) or 0.7),
    )


def create_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_mode == "litellm":
        return LiteLLMClient(settings)
    return MockLLMClient(settings)
