from __future__ import annotations

import re
from dataclasses import replace

from backend.app.core.schemas import PolicyChunk, ReviewFinding, ReviewRequest, ReviewRoute

ROUTE_MAX_FINDINGS = {
    "simple_failure_review": 3,
    "policy_context_review": 6,
    "deep_quality_review": 8,
}

SEVERITY_ALIASES = {
    "critical": "high",
    "blocker": "high",
    "p0": "high",
    "p1": "high",
    "major": "high",
    "high": "high",
    "p2": "medium",
    "minor": "medium",
    "medium": "medium",
    "p3": "low",
    "trivial": "low",
    "info": "low",
    "low": "low",
}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
HUNK_HEADER_PATTERN = re.compile(
    r"^@@\s+-\d+(?:,\d+)?\s+\+(?P<start>\d+)(?:,(?P<count>\d+))?\s+@@"
)


def _right_side_diff_lines(patch: str) -> set[int]:
    lines: set[int] = set()
    current_line: int | None = None
    for raw_line in patch.splitlines():
        header = HUNK_HEADER_PATTERN.match(raw_line)
        if header:
            current_line = int(header.group("start"))
            continue
        if current_line is None or raw_line.startswith("\\ No newline"):
            continue
        if raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
        lines.add(current_line)
        current_line += 1
    return lines


def _canonical_policy_sources(policies: list[PolicyChunk]) -> dict[str, str]:
    sources: dict[str, str] = {}
    for policy in policies:
        canonical = f"{policy.source_path}#{policy.section_title}"
        sources[canonical] = canonical
        sources.setdefault(policy.source_path, canonical)
    return sources


def _finding_key(finding: ReviewFinding) -> tuple[object, ...]:
    normalized_message = " ".join(finding.message.lower().split())
    return (
        finding.file_path,
        finding.line_start,
        finding.category.lower(),
        normalized_message,
    )


def validate_and_rank_findings(
    request: ReviewRequest,
    route: ReviewRoute,
    policies: list[PolicyChunk],
    findings: list[ReviewFinding],
) -> tuple[list[ReviewFinding], dict[str, int]]:
    changed_files = {changed_file.path: changed_file for changed_file in request.changed_files}
    policy_sources = _canonical_policy_sources(policies)
    report = {
        "received": len(findings),
        "accepted": 0,
        "unknown_file_dropped": 0,
        "empty_finding_dropped": 0,
        "duplicate_dropped": 0,
        "invalid_line_removed": 0,
        "invalid_policy_source_removed": 0,
        "over_limit_dropped": 0,
    }
    accepted: list[ReviewFinding] = []
    seen: set[tuple[object, ...]] = set()

    for finding in findings:
        changed_file = changed_files.get(finding.file_path)
        if changed_file is None:
            report["unknown_file_dropped"] += 1
            continue
        if not finding.message.strip() or not finding.suggestion.strip():
            report["empty_finding_dropped"] += 1
            continue

        severity = SEVERITY_ALIASES.get(finding.severity.strip().lower(), "medium")
        policy_source = finding.policy_source
        if policy_source:
            canonical_source = policy_sources.get(policy_source)
            if canonical_source is None:
                policy_source = None
                report["invalid_policy_source_removed"] += 1
            else:
                policy_source = canonical_source

        line_start = finding.line_start
        line_end = finding.line_end
        if line_start is not None:
            valid_lines = _right_side_diff_lines(changed_file.patch)
            if line_start not in valid_lines:
                line_start = None
                line_end = None
                report["invalid_line_removed"] += 1
            elif line_end is None or line_end < line_start or line_end not in valid_lines:
                line_end = line_start

        normalized = replace(
            finding,
            severity=severity,
            category=finding.category.strip().lower() or "general",
            line_start=line_start,
            line_end=line_end,
            message=finding.message.strip(),
            suggestion=finding.suggestion.strip(),
            policy_source=policy_source,
            confidence=max(0.0, min(float(finding.confidence), 1.0)),
        )
        key = _finding_key(normalized)
        if key in seen:
            report["duplicate_dropped"] += 1
            continue
        seen.add(key)
        accepted.append(normalized)

    accepted.sort(
        key=lambda finding: (
            SEVERITY_ORDER.get(finding.severity, 1),
            -finding.confidence,
            finding.file_path,
            finding.line_start or 0,
        )
    )
    max_findings = ROUTE_MAX_FINDINGS.get(route.name, 6)
    if len(accepted) > max_findings:
        report["over_limit_dropped"] = len(accepted) - max_findings
        accepted = accepted[:max_findings]
    report["accepted"] = len(accepted)
    return accepted, report
