from __future__ import annotations

import json

from backend.app.core.routing import HIGH_RISK_PATH_KEYWORDS
from backend.app.core.schemas import ChangedFilePayload, PolicyChunk, ReviewRequest, ReviewRoute
from backend.app.core.security import mask_secrets


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
) -> tuple[list[dict[str, object]], dict[str, object]]:
    max_files, max_patch_chars = ROUTE_PROMPT_BUDGETS.get(route.name, (20, 30_000))
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
    return snapshots, {
        "total_files": len(request.changed_files),
        "included_files": len(snapshots),
        "files_truncated": len(snapshots) < len(request.changed_files),
        "patch_char_budget": max_patch_chars,
    }


def build_review_messages(
    request: ReviewRequest,
    route: ReviewRoute,
    policies: list[PolicyChunk],
) -> list[dict[str, str]]:
    changed_files, prompt_scope = _changed_file_snapshot(request, route)
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

    system = (
        "You are an AI code review agent for GitHub Pull Requests. "
        "Write all summaries, findings, and suggestions in Korean and return only valid JSON. "
        "Every finding must be grounded in diff, check logs, or provided repository policy. "
        "Do not invent unavailable files, line numbers, policies, or execution behavior."
    )
    user = {
        "route": route.to_dict(),
        "review_instructions": _route_instructions(route),
        "quality_instructions": REVIEW_QUALITY_INSTRUCTIONS,
        "severity_guide": {
            "high": "merge-blocking risk",
            "medium": "bounded real defect",
            "low": "evidence-backed maintainability or test weakness",
        },
        "max_findings": {
            "simple_failure_review": 3,
            "policy_context_review": 6,
            "deep_quality_review": 8,
        }.get(route.name, 6),
        "review_payload": payload,
        "output_schema": {
            "summary": {
                "overall_risk": "low|medium|high",
                "short_comment": "brief PR-level review summary",
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
                    "confidence": 0.0,
                }
            ],
        },
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
    ]
