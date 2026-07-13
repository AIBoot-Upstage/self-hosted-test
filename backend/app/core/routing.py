from __future__ import annotations

from backend.app.core.schemas import CheckResultPayload, PullRequestFeatures, ReviewRequest, ReviewRoute

HIGH_RISK_PATH_KEYWORDS = {
    "auth",
    "iam",
    "jwt",
    "oauth",
    "permission",
    "policy",
    "security",
    "secret",
    "token",
    "payment",
    "billing",
    "migration",
    "migrations",
    "schema",
    "sql",
    "terraform",
    "infra",
    "docker",
    ".github/workflows",
}


def _status_for(checks: list[CheckResultPayload], kind: str) -> str:
    matched = [check for check in checks if kind in check.kind.lower()]
    if not matched:
        return "unknown"
    if any(check.is_failed for check in matched):
        return "failed"
    if any(check.is_passed for check in matched):
        return "passed"
    return "skipped"


def _risk_files(request: ReviewRequest) -> list[str]:
    risk_files: list[str] = []
    for changed_file in request.changed_files:
        normalized = changed_file.path.lower()
        if any(keyword in normalized for keyword in HIGH_RISK_PATH_KEYWORDS):
            risk_files.append(changed_file.path)
            continue
        patch = changed_file.patch.lower()
        if any(marker in patch for marker in ("password", "token", "secret", "permission")):
            risk_files.append(changed_file.path)
    return sorted(set(risk_files))


def _quality_review_reasons(features: PullRequestFeatures) -> list[str]:
    reasons = ["checks passed or no failing check detected"]
    if features.policy_available:
        reasons.append("repository policy is available")
    else:
        reasons.append("repository policy is unavailable; falling back to general review")
    if features.has_high_risk_files:
        reasons.append("high-risk signals detected; deep review can be requested")
    if features.changed_lines > 600:
        reasons.append("large diff detected; deep review can be requested")
    if features.changed_files_count > 20:
        reasons.append("many changed files detected; deep review can be requested")
    return reasons


def extract_features(request: ReviewRequest, policy_available: bool) -> PullRequestFeatures:
    syntax_status = _status_for(request.checks, "syntax")
    lint_status = _status_for(request.checks, "lint")
    test_status = _status_for(request.checks, "test")
    changed_lines = sum(changed_file.changed_lines for changed_file in request.changed_files)
    risk_files = _risk_files(request)

    confidence = 0.9
    if syntax_status == "unknown":
        confidence -= 0.08
    if lint_status == "unknown":
        confidence -= 0.08
    if test_status == "unknown":
        confidence -= 0.08
    if not policy_available:
        confidence -= 0.06
    if risk_files:
        confidence -= 0.05

    return PullRequestFeatures(
        syntax_status=syntax_status,
        lint_status=lint_status,
        test_status=test_status,
        changed_files_count=len(request.changed_files),
        changed_lines=changed_lines,
        risk_files=risk_files,
        policy_available=policy_available,
        router_confidence=max(0.1, round(confidence, 2)),
    )


def select_route(features: PullRequestFeatures, review_mode: str = "auto") -> ReviewRoute:
    if features.syntax_failed or features.lint_failed or features.test_failed:
        return ReviewRoute(
            name="simple_failure_review",
            model_tier="solar3-low",
            use_rag=False,
            focus=["failure_summary", "likely_cause", "fix_priority"],
            reasons=["syntax, lint, or test failed"],
            confidence=0.95,
        )

    if review_mode == "deep_quality_review":
        return ReviewRoute(
            name="deep_quality_review",
            model_tier="solar3-high",
            use_rag=features.policy_available,
            focus=[
                "architecture",
                "security",
                "time_complexity",
                "space_complexity",
                "simplification",
                "maintainability",
            ],
            reasons=["manual deep review requested"],
            confidence=max(0.7, features.router_confidence),
        )

    return ReviewRoute(
        name="policy_context_review",
        model_tier="solar3-medium",
        use_rag=features.policy_available,
        focus=["repo_policy", "style", "tests", "api_contract"],
        reasons=_quality_review_reasons(features),
        confidence=features.router_confidence,
    )
