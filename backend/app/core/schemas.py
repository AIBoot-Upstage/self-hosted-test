from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

JsonDict = dict[str, Any]


def _string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RepositoryPayload:
    provider: str
    owner: str
    name: str
    default_branch: str = "main"

    @classmethod
    def from_dict(cls, payload: JsonDict) -> "RepositoryPayload":
        return cls(
            provider=_string(payload.get("provider"), "github"),
            owner=_string(payload.get("owner")),
            name=_string(payload.get("name")),
            default_branch=_string(payload.get("default_branch"), "main"),
        )

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass(frozen=True)
class PullRequestPayload:
    number: int
    title: str
    author: str
    base_sha: str
    head_sha: str
    base_branch: str
    head_branch: str

    @classmethod
    def from_dict(cls, payload: JsonDict) -> "PullRequestPayload":
        return cls(
            number=_int(payload.get("number")),
            title=_string(payload.get("title")),
            author=_string(payload.get("author")),
            base_sha=_string(payload.get("base_sha")),
            head_sha=_string(payload.get("head_sha")),
            base_branch=_string(payload.get("base_branch"), "main"),
            head_branch=_string(payload.get("head_branch")),
        )


@dataclass(frozen=True)
class CheckResultPayload:
    kind: str
    status: str
    conclusion: str
    summary: str = ""
    log_uri: str | None = None

    @classmethod
    def from_dict(cls, payload: JsonDict) -> "CheckResultPayload":
        return cls(
            kind=_string(payload.get("kind"), "unknown"),
            status=_string(payload.get("status"), "unknown"),
            conclusion=_string(payload.get("conclusion"), "unknown"),
            summary=_string(payload.get("summary")),
            log_uri=payload.get("log_uri"),
        )

    @property
    def is_failed(self) -> bool:
        failed_values = {"failed", "failure", "error", "timed_out", "cancelled"}
        return self.status.lower() in failed_values or self.conclusion.lower() in failed_values

    @property
    def is_passed(self) -> bool:
        passed_values = {"passed", "success", "completed"}
        return self.conclusion.lower() in passed_values or (
            self.status.lower() == "completed" and self.conclusion.lower() == "success"
        )


@dataclass(frozen=True)
class ChangedFilePayload:
    path: str
    status: str = "modified"
    additions: int = 0
    deletions: int = 0
    patch: str = ""

    @classmethod
    def from_dict(cls, payload: JsonDict) -> "ChangedFilePayload":
        return cls(
            path=_string(payload.get("path") or payload.get("filename")),
            status=_string(payload.get("status"), "modified"),
            additions=_int(payload.get("additions")),
            deletions=_int(payload.get("deletions")),
            patch=_string(payload.get("patch")),
        )

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions


@dataclass(frozen=True)
class GitHubPayload:
    run_id: str = ""
    event_name: str = "pull_request"
    delivery_id: str = ""
    installation_id: str = ""

    @classmethod
    def from_dict(cls, payload: JsonDict | None) -> "GitHubPayload":
        payload = payload or {}
        return cls(
            run_id=_string(payload.get("run_id")),
            event_name=_string(payload.get("event_name"), "pull_request"),
            delivery_id=_string(payload.get("delivery_id")),
            installation_id=_string(payload.get("installation_id")),
        )


@dataclass(frozen=True)
class ReviewRequest:
    repository: RepositoryPayload
    pull_request: PullRequestPayload
    checks: list[CheckResultPayload] = field(default_factory=list)
    changed_files: list[ChangedFilePayload] = field(default_factory=list)
    github: GitHubPayload = field(default_factory=GitHubPayload)

    @classmethod
    def from_dict(cls, payload: JsonDict) -> "ReviewRequest":
        return cls(
            repository=RepositoryPayload.from_dict(payload.get("repository", {})),
            pull_request=PullRequestPayload.from_dict(payload.get("pull_request", {})),
            checks=[CheckResultPayload.from_dict(item) for item in payload.get("checks", [])],
            changed_files=[
                ChangedFilePayload.from_dict(item) for item in payload.get("changed_files", [])
            ],
            github=GitHubPayload.from_dict(payload.get("github")),
        )

    def idempotency_key(self) -> str:
        return (
            f"{self.repository.provider}:"
            f"{self.repository.full_name}:"
            f"{self.pull_request.number}:"
            f"{self.pull_request.head_sha}"
        )

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class PullRequestFeatures:
    syntax_status: str
    lint_status: str
    test_status: str
    changed_files_count: int
    changed_lines: int
    risk_files: list[str]
    policy_available: bool
    router_confidence: float

    @property
    def syntax_failed(self) -> bool:
        return self.syntax_status == "failed"

    @property
    def lint_failed(self) -> bool:
        return self.lint_status == "failed"

    @property
    def test_failed(self) -> bool:
        return self.test_status == "failed"

    @property
    def has_high_risk_files(self) -> bool:
        return bool(self.risk_files)

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ReviewRoute:
    name: str
    model_tier: str
    use_rag: bool
    focus: list[str]
    reasons: list[str]
    confidence: float

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class PolicyChunk:
    source_path: str
    section_title: str
    content: str
    policy_type: str = "general"
    score: float = 0.0

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ReviewFinding:
    severity: str
    category: str
    file_path: str
    line_start: int | None
    line_end: int | None
    message: str
    suggestion: str
    evidence: JsonDict = field(default_factory=dict)
    policy_source: str | None = None
    confidence: float = 0.7

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ReviewSummary:
    route_name: str
    model_tier: str
    overall_risk: str
    short_comment: str

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ModelCallUsage:
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    status: str = "completed"
    reasoning_effort: str | None = None
    cost_usd: float = 0.0

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ReviewEvent:
    review_run_id: str
    sequence: int
    event_type: str
    payload: JsonDict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class ReviewResult:
    review_run_id: str
    status: str
    idempotency_key: str
    summary: ReviewSummary
    findings: list[ReviewFinding]
    route: ReviewRoute
    features: PullRequestFeatures
    model_call: ModelCallUsage
    retrieved_policies: list[PolicyChunk] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> JsonDict:
        return {
            "review_run_id": self.review_run_id,
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "summary": self.summary.to_dict(),
            "findings": [finding.to_dict() for finding in self.findings],
            "route": self.route.to_dict(),
            "features": self.features.to_dict(),
            "model_call": self.model_call.to_dict(),
            "retrieved_policies": [chunk.to_dict() for chunk in self.retrieved_policies],
            "created_at": self.created_at,
        }
