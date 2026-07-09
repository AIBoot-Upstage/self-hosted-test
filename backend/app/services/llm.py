from __future__ import annotations

import json
import os
import time
from typing import Any, Protocol

from backend.app.core.config import Settings
from backend.app.core.schemas import (
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
    ) -> tuple[ReviewSummary, list[ReviewFinding], ModelCallUsage]:
        start = time.perf_counter()
        findings: list[ReviewFinding] = []
        file_path = _first_changed_path(request)
        line = _line_for_first_file(request)

        if route.name == "simple_failure_review":
            failed_checks = [check for check in request.checks if check.is_failed]
            summary_text = "One or more checks failed. Review the failing log before merging."
            if failed_checks:
                summary_text = f"{failed_checks[0].kind} failed: {failed_checks[0].summary[:160]}"
            findings.append(
                ReviewFinding(
                    severity="high",
                    category="failure",
                    file_path=file_path,
                    line_start=line,
                    line_end=line,
                    message="The PR has a failing check, so the first priority is to fix CI.",
                    suggestion="Open the failing check log and verify the changed file related to the error.",
                    evidence={"failed_checks": [check.kind for check in failed_checks]},
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
                    message="This change touches a high-risk area or has a large diff.",
                    suggestion=(
                        "Ask for an additional human review focused on authorization, data consistency, "
                        "rollback behavior, and production observability."
                    ),
                    evidence={"route_reasons": route.reasons},
                    policy_source=policies[0].source_path if policies else None,
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
                        message="Repository policy context is relevant to this PR.",
                        suggestion=(
                            "Compare the changed code with the referenced policy section and adjust naming, "
                            "tests, or API behavior if they diverge."
                        ),
                        evidence={
                            "section_title": policy.section_title,
                            "policy_score": policy.score,
                        },
                        policy_source=f"{policy.source_path}#{policy.section_title}",
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
                        message="No repository policy was available, so only a general review was generated.",
                        suggestion="Add policies under POLICY_ROOT (default policies/) for stronger review context.",
                        confidence=0.62,
                    )
                )

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
            short_comment=summary_text if route.name == "simple_failure_review" else (
                f"{route.name} completed with {len(findings)} finding(s)."
            ),
        )
        return summary, findings, usage


class LiteLLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._langfuse_ready = False

    def _ensure_langfuse_callback(self) -> None:
        if (
            self._langfuse_ready
            or not self.settings.langfuse_public_key
            or not self.settings.langfuse_secret_key
        ):
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
        completion_kwargs: dict[str, Any] = {
            "model": litellm_model,
            "messages": messages,
            "api_key": self.settings.upstage_api_key,
            "api_base": self.settings.upstage_api_base_url,
            "temperature": 0.1,
            "timeout": 90,
            "metadata": {
                "review_run_id": review_run_id,
                "route_name": route.name,
                "model_tier": route.model_tier,
                "repository": request.repository.full_name,
                "pull_request_number": request.pull_request.number,
            },
        }
        if reasoning_effort:
            completion_kwargs["reasoning_effort"] = reasoning_effort
            completion_kwargs["allowed_openai_params"] = ["reasoning_effort"]
        response = completion(**completion_kwargs)
        latency_ms = int((time.perf_counter() - start) * 1000)
        content = response.choices[0].message.content
        parsed = _parse_json(content)

        summary_payload = parsed.get("summary", {})
        findings_payload = parsed.get("findings", [])
        summary = ReviewSummary(
            route_name=route.name,
            model_tier=route.model_tier,
            overall_risk=str(summary_payload.get("overall_risk", "medium")),
            short_comment=str(summary_payload.get("short_comment", "Review completed.")),
        )
        findings = [_finding_from_payload(item) for item in findings_payload]
        usage_payload = getattr(response, "usage", None)
        usage = ModelCallUsage(
            provider="upstage",
            model=model,
            prompt_tokens=int(getattr(usage_payload, "prompt_tokens", 0) or 0),
            completion_tokens=int(getattr(usage_payload, "completion_tokens", 0) or 0),
            latency_ms=latency_ms,
            reasoning_effort=reasoning_effort,
        )
        return summary, findings, usage


def _litellm_model_id(model: str, api_base: str | None) -> str:
    if "/" in model:
        return model
    if api_base:
        return f"openai/{model}"
    return model


def _parse_json(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(content[start : end + 1])


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
        confidence=float(payload.get("confidence", 0.7) or 0.7),
    )


def create_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_mode == "litellm":
        return LiteLLMClient(settings)
    return MockLLMClient(settings)
