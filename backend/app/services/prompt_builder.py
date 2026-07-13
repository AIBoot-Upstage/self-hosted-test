from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace

from backend.app.core.routing import HIGH_RISK_PATH_KEYWORDS
from backend.app.core.schemas import (
    ChangedFilePayload,
    PolicyChunk,
    ReviewHarnessContext,
    ReviewRequest,
    ReviewRoute,
)
from backend.app.core.security import mask_secrets
from backend.app.services.policy_harness import PolicyHarness
from backend.app.services.rag import rank_policy_chunks


def _route_instructions(route: ReviewRoute) -> list[str]:
    if route.name == "simple_failure_review":
        return [
            "Focus on the failing syntax, lint, or test evidence and the smallest actionable fix.",
            "Do not expand the review into unrelated architecture or style commentary.",
        ]
    if route.name == "deep_quality_review":
        return [
            (
                "Provide an independent second perspective instead of repeating the standard "
                "policy review."
            ),
            (
                "Analyze time complexity for changed execution paths when the diff provides "
                "enough evidence; name the input variable and estimated Big-O."
            ),
            (
                "Analyze space complexity and memory growth when collections, caching, "
                "buffering, recursion, or large payloads are affected."
            ),
            (
                "Look for behavior-preserving simplification: duplicated branches, unnecessary "
                "state, avoidable queries or loops, and smaller interfaces."
            ),
            (
                "Consider architecture, security, failure isolation, maintainability, and "
                "operational impact."
            ),
            (
                "Do not invent complexity problems. Omit a category when the supplied diff is "
                "insufficient to support a finding."
            ),
        ]
    return [
        "Treat retrieved repository policies as the authoritative review criteria.",
        "Cite policy_source exactly as supplied when a finding is grounded in a retrieved policy.",
        "Do not cite a policy that does not directly support the finding.",
    ]


REVIEW_QUALITY_INSTRUCTIONS = [
    (
        "Report only actionable issues introduced by the diff; prioritize correctness, security, "
        "data integrity, and reliability."
    ),
    (
        "Each finding must name its trigger, consequence, concrete diff evidence, smallest fix, "
        "and focused verification."
    ),
    (
        "Use only right-side diff lines; omit praise, repeated CI output, subjective style, and "
        "speculation."
    ),
]


ROUTE_PROMPT_BUDGETS = {
    "simple_failure_review": (8, 12_000),
    "policy_context_review": (20, 30_000),
    "deep_quality_review": (30, 50_000),
}

ROUTE_BATCH_BUDGETS = {
    "simple_failure_review": (4, 6_000),
    "policy_context_review": (4, 6_000),
    "deep_quality_review": (4, 7_000),
}


@dataclass(frozen=True)
class ReviewPromptBatch:
    request: ReviewRequest
    messages: list[dict[str, str]]
    policies: list[PolicyChunk]
    harness: ReviewHarnessContext | None
    index: int
    count: int
    patch_chars: int

REVIEW_SIGNAL_MARKERS = {
    "authorization",
    "credential",
    "database",
    "execute(",
    "permission",
    "secret",
    "subprocess",
    "token",
}


def _file_review_priority(changed_file: ChangedFilePayload) -> tuple[bool, bool, int]:
    path = changed_file.path.lower()
    patch = changed_file.patch.lower()
    return (
        any(keyword in path for keyword in HIGH_RISK_PATH_KEYWORDS),
        any(marker in patch for marker in REVIEW_SIGNAL_MARKERS),
        changed_file.changed_lines,
    )


def _changed_file_snapshot(
    request: ReviewRequest,
    route: ReviewRoute,
    budget: tuple[int, int] | None = None,
    prompt_context: dict[str, object] | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    max_files, max_patch_chars = budget or ROUTE_PROMPT_BUDGETS.get(
        route.name,
        (20, 30_000),
    )
    selected_files = sorted(
        request.changed_files,
        key=_file_review_priority,
        reverse=True,
    )[:max_files]
    remaining_patch_chars = max_patch_chars
    snapshots: list[dict[str, object]] = []
    for changed_file in selected_files:
        patch = changed_file.patch[: min(4000, remaining_patch_chars)]
        remaining_patch_chars -= len(patch)
        snapshots.append(
            {
                "path": changed_file.path,
                "status": changed_file.status,
                "additions": changed_file.additions,
                "deletions": changed_file.deletions,
                "patch": mask_secrets(patch),
                "patch_truncated": len(patch) < len(changed_file.patch),
            }
        )
        if remaining_patch_chars <= 0:
            break
    scope: dict[str, object] = {
        "total_files": len(request.changed_files),
        "included_files": len(snapshots),
        "files_truncated": len(snapshots) < len(request.changed_files),
        "patch_char_budget": max_patch_chars,
    }
    if prompt_context:
        scope.update(prompt_context)
    return snapshots, scope


def _selected_files_for_review(
    request: ReviewRequest,
    route: ReviewRoute,
) -> list[ChangedFilePayload]:
    max_files, max_patch_chars = ROUTE_PROMPT_BUDGETS.get(route.name, (20, 30_000))
    candidates = sorted(request.changed_files, key=_file_review_priority, reverse=True)[:max_files]
    selected: list[ChangedFilePayload] = []
    remaining_patch_chars = max_patch_chars
    for changed_file in candidates:
        if changed_file.patch:
            if remaining_patch_chars <= 0:
                continue
            patch = changed_file.patch[: min(4000, remaining_patch_chars)]
            remaining_patch_chars -= len(patch)
            selected.append(replace(changed_file, patch=patch))
        else:
            selected.append(changed_file)
    return selected


def _group_files(
    changed_files: list[ChangedFilePayload],
    max_files: int,
    max_patch_chars: int,
) -> list[list[ChangedFilePayload]]:
    groups: list[list[ChangedFilePayload]] = []
    current: list[ChangedFilePayload] = []
    current_patch_chars = 0
    for changed_file in changed_files:
        patch_chars = len(changed_file.patch)
        if current and (
            len(current) >= max_files or current_patch_chars + patch_chars > max_patch_chars
        ):
            groups.append(current)
            current = []
            current_patch_chars = 0
        current.append(changed_file)
        current_patch_chars += patch_chars
    if current:
        groups.append(current)
    return groups


def build_review_messages(
    request: ReviewRequest,
    route: ReviewRoute,
    policies: list[PolicyChunk],
    budget: tuple[int, int] | None = None,
    prompt_context: dict[str, object] | None = None,
    harness: ReviewHarnessContext | None = None,
) -> list[dict[str, str]]:
    changed_files, prompt_scope = _changed_file_snapshot(
        request,
        route,
        budget=budget,
        prompt_context=prompt_context,
    )
    payload = {
        "repository": request.repository.full_name,
        "pull_request": {
            "number": request.pull_request.number,
            "title": request.pull_request.title,
            "author": request.pull_request.author,
            "base_sha": request.pull_request.base_sha,
            "head_sha": request.pull_request.head_sha,
        },
        "checks": [
            {
                "kind": check.kind,
                "status": check.status,
                "conclusion": check.conclusion,
                "summary": mask_secrets(check.summary[:3000]),
            }
            for check in request.checks
        ],
        "changed_files": changed_files,
        "prompt_scope": prompt_scope,
        "policies": [
            {
                "source_path": policy.source_path,
                "section_title": policy.section_title,
                "policy_type": policy.policy_type,
                "content": policy.content[:2500],
                "policy_source": f"{policy.source_path}#{policy.section_title}",
                "retrieval_score": policy.score,
            }
            for policy in policies
        ],
    }
    route_max_findings = {
        "simple_failure_review": 3,
        "policy_context_review": 6,
        "deep_quality_review": 8,
    }.get(route.name, 6)
    batch_count = max(1, int((prompt_context or {}).get("batch_count", 1)))
    batch_max_findings = max(1, math.ceil(route_max_findings / batch_count))

    system = (
        "You are an AI code review agent for GitHub Pull Requests. "
        "Return only valid JSON. Every natural-language value in summary, file_summaries, "
        "findings, suggestions, and evidence MUST be a complete Korean sentence. "
        "English is allowed only for code identifiers, file paths, API names, and policy IDs. "
        "Describe concrete code and behavior changes instead of abstract importance or risk. "
        "Every finding must be grounded in diff, check logs, or provided repository policy. "
        "Do not invent unavailable files, line numbers, policies, or execution behavior."
    )
    user = {
        "route": route.to_dict(),
        "review_instructions": _route_instructions(route),
        "review_harness": harness.to_dict() if harness is not None else None,
        "review_harness_instructions": [
            "knowledge_cards는 저명한 외부 출처에서 정제한 검토 관점이며 repository 정책이 아니다.",
            "각 card는 evidence_required를 diff에서 확인하고 false_positive_guard를 통과할 때만 사용한다.",
            "card만으로 결함을 단정하거나 source_ids를 policy_source에 쓰지 않는다.",
            "card에서 파생한 finding의 severity는 severity_cap을 넘지 않는다.",
            "모든 finding은 근거로 사용한 선택 card의 card_id를 knowledge_card_id에 정확히 기록한다.",
            "skill_id, card title, source_id를 knowledge_card_id 대신 사용하지 않는다.",
            "evidence_required를 diff에서 입증하지 못하거나 false_positive_guard에 해당하면 finding을 생략한다.",
            "제공된 diff에 코드가 없다는 사실만으로 저장소 전체에 검증, 예외 처리, 테스트가 없다고 단정하지 않는다.",
            "선택 개선안이 아니라 현재 diff가 만드는 재현 가능한 잘못된 동작만 finding으로 작성한다.",
            "finding을 만들기 전에 제공된 모든 line에서 할당, 기본 반환, 검증, fallback 등 주장을 반증하는 코드를 찾고 하나라도 있으면 생략한다.",
            "max_findings는 목표 개수가 아닌 상한이며 입증된 결함이 없으면 빈 findings가 올바른 응답이다.",
            "외부 API·network·LLM 직접 호출 또는 flaky test 주장은 해당 client 호출과 mock·fake 부재가 diff에 함께 보일 때만 작성한다.",
        ],
        "finding_contract": {
            "allowed_knowledge_card_ids": (
                [card.card_id for card in harness.knowledge_cards] if harness else []
            ),
            "rule": (
                "각 finding은 위 목록에서 정확히 하나를 knowledge_card_id로 사용한다. "
                "적용 가능한 card가 없으면 finding을 만들지 않는다."
            ),
        },
        "language_contract": {
            "locale": "ko-KR",
            "rule": (
                "summary.change_summary, summary.short_comment, "
                "summary.file_summaries[*].change_summary와 모든 리뷰 설명은 반드시 한국어로 쓴다."
            ),
        },
        "quality_instructions": REVIEW_QUALITY_INSTRUCTIONS,
        "summary_instructions": [
            "change_summary는 이 배치에서 실제로 바뀐 동작, 인터페이스, 데이터 흐름을 구체적으로 요약한다.",
            "file_summaries는 review_payload.changed_files의 모든 파일을 입력 순서대로 한 번씩 포함한다.",
            "파일 경로는 입력값을 정확히 복사하고 입력에 없는 경로를 만들지 않는다.",
            "중요하다, 위험하다 같은 추상적 평가 대신 무엇이 어떻게 변경됐는지 작성한다.",
        ],
        "severity_guide": {
            "high": "merge-blocking risk",
            "medium": "bounded real defect",
            "low": "evidence-backed maintainability or test weakness",
        },
        "max_findings": batch_max_findings,
        "review_payload": payload,
        "output_schema": {
            "summary": {
                "overall_risk": "low|medium|high",
                "short_comment": "체크 실행 결과에 표시할 한 문장 변경 요약",
                "change_summary": "구체적인 배치 단위 변경 요약",
                "file_summaries": [
                    {
                        "file_path": "review_payload.changed_files에 있는 정확한 경로",
                        "change_summary": "해당 파일에서 실제로 변경된 내용",
                    }
                ],
            },
            "findings": [
                {
                    "severity": "low|medium|high",
                    "category": (
                        "functional_correctness|security|data_integrity|reliability|performance|"
                        "test|api_contract|architecture|time_complexity|space_complexity|"
                        "simplification|maintainability"
                    ),
                    "file_path": "path/to/file.py",
                    "line_start": 1,
                    "line_end": 1,
                    "message": "specific issue",
                    "suggestion": "specific improvement",
                    "evidence": {
                        "trigger": "input, state, or execution condition",
                        "consequence": "observable failure or maintenance cost",
                        "supporting_context": "specific diff or check evidence",
                    },
                    "policy_source": "optional policy source",
                    "knowledge_card_id": "finding_contract.allowed_knowledge_card_ids 중 정확히 하나",
                    "confidence": 0.0,
                }
            ],
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]


def build_review_prompt_batches(
    request: ReviewRequest,
    route: ReviewRoute,
    policies: list[PolicyChunk],
    policy_harness: PolicyHarness | None = None,
) -> list[ReviewPromptBatch]:
    selected_files = _selected_files_for_review(request, route)
    max_batch_files, max_batch_patch_chars = ROUTE_BATCH_BUDGETS.get(
        route.name,
        (4, 6_000),
    )
    file_groups = _group_files(selected_files, max_batch_files, max_batch_patch_chars)
    if not file_groups:
        file_groups = [[]]

    batch_count = len(file_groups)
    batches: list[ReviewPromptBatch] = []
    for offset, changed_files in enumerate(file_groups):
        batch_index = offset + 1
        batch_request = replace(request, changed_files=changed_files)
        patch_chars = sum(len(changed_file.patch) for changed_file in changed_files)
        batch_harness = policy_harness.select(batch_request, route) if policy_harness else None
        if policy_harness and route.use_rag:
            batch_policies = rank_policy_chunks(
                policies,
                batch_request,
                top_k=policy_harness.max_policies_per_batch,
                policy_types=set(batch_harness.policy_types) or None,
            )
        else:
            batch_policies = policies
        messages = build_review_messages(
            batch_request,
            route,
            batch_policies,
            budget=(max_batch_files, max_batch_patch_chars),
            prompt_context={
                "original_total_files": len(request.changed_files),
                "selected_total_files": len(selected_files),
                "batch_index": batch_index,
                "batch_count": batch_count,
            },
            harness=batch_harness,
        )
        batches.append(
            ReviewPromptBatch(
                request=batch_request,
                messages=messages,
                policies=batch_policies,
                harness=batch_harness,
                index=batch_index,
                count=batch_count,
                patch_chars=patch_chars,
            )
        )
    return batches
