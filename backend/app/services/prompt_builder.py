from __future__ import annotations

import json

from backend.app.core.schemas import PolicyChunk, ReviewRequest, ReviewRoute
from backend.app.core.security import mask_secrets


def _changed_file_snapshot(request: ReviewRequest) -> list[dict[str, object]]:
    return [
        {
            "path": changed_file.path,
            "status": changed_file.status,
            "additions": changed_file.additions,
            "deletions": changed_file.deletions,
            "patch": mask_secrets(changed_file.patch[:4000]),
        }
        for changed_file in request.changed_files
    ]


def build_review_messages(
    request: ReviewRequest,
    route: ReviewRoute,
    policies: list[PolicyChunk],
) -> list[dict[str, str]]:
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
        "changed_files": _changed_file_snapshot(request),
        "policies": [
            {
                "source_path": policy.source_path,
                "section_title": policy.section_title,
                "policy_type": policy.policy_type,
                "content": policy.content[:2500],
            }
            for policy in policies
        ],
    }

    system = (
        "You are an AI code review agent for GitHub Pull Requests. "
        "Return only valid JSON. Every finding must be grounded in diff, check logs, "
        "or provided repository policy. Do not invent unavailable line numbers."
    )
    user = {
        "route": route.to_dict(),
        "review_payload": payload,
        "output_schema": {
            "summary": {
                "overall_risk": "low|medium|high",
                "short_comment": "brief PR-level review summary",
            },
            "findings": [
                {
                    "severity": "low|medium|high",
                    "category": "failure|style|test|api_contract|security|architecture",
                    "file_path": "path/to/file.py",
                    "line_start": 1,
                    "line_end": 1,
                    "message": "specific issue",
                    "suggestion": "specific improvement",
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

