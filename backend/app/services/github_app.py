from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from backend.app.core.config import Settings
from backend.app.core.schemas import ReviewRequest


class GitHubWebhookError(ValueError):
    pass


def verify_github_signature(
    payload_body: bytes,
    secret: str | None,
    signature_header: str | None,
) -> None:
    if not secret:
        raise RuntimeError("GITHUB_WEBHOOK_SECRET is required for GitHub webhook delivery")
    if not signature_header:
        raise GitHubWebhookError("X-Hub-Signature-256 header is required")

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        msg=payload_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise GitHubWebhookError("GitHub webhook signature mismatch")


def normalize_private_key(raw_value: str) -> str:
    value = raw_value.strip()
    candidate = value.replace("\\n", "\n")
    if "PRIVATE KEY" in candidate:
        return candidate.strip() + "\n"

    compact = re.sub(r"\s+", "", value)
    try:
        decoded = base64.b64decode(compact, validate=True).decode("utf-8")
    except Exception as exc:
        raise RuntimeError(
            "GITHUB_APP_PRIVATE_KEY must be a PEM private key, escaped PEM, or base64 PEM"
        ) from exc

    if "PRIVATE KEY" not in decoded:
        raise RuntimeError("GITHUB_APP_PRIVATE_KEY does not contain a private key")
    return decoded.strip() + "\n"


class GitHubAppClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def create_jwt(self) -> str:
        try:
            import jwt
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError("PyJWT is required for GitHub App authentication") from exc

        if not self.settings.github_app_id:
            raise RuntimeError("GITHUB_APP_ID is required for GitHub App authentication")

        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + 540,
            "iss": self.settings.github_app_id,
        }
        return jwt.encode(payload, self._private_key(), algorithm="RS256")

    def installation_token(self, installation_id: str | int) -> str:
        payload = self.request_json(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            token=self.create_jwt(),
        )
        token = str(payload.get("token", ""))
        if not token:
            raise RuntimeError("GitHub installation access token response did not include token")
        return token

    def get_pull_request(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        token: str,
    ) -> dict[str, Any]:
        return self.request_json(
            "GET",
            f"/repos/{_quote(owner)}/{_quote(repo)}/pulls/{pull_number}",
            token=token,
        )

    def list_pull_files(
        self,
        owner: str,
        repo: str,
        pull_number: int,
        token: str,
    ) -> list[dict[str, Any]]:
        return self.paginated_get(
            f"/repos/{_quote(owner)}/{_quote(repo)}/pulls/{pull_number}/files",
            token=token,
        )

    def list_check_runs(
        self,
        owner: str,
        repo: str,
        ref: str,
        token: str,
    ) -> list[dict[str, Any]]:
        payload = self.request_json(
            "GET",
            f"/repos/{_quote(owner)}/{_quote(repo)}/commits/{_quote(ref)}/check-runs",
            token=token,
        )
        check_runs = payload.get("check_runs", [])
        return check_runs if isinstance(check_runs, list) else []

    def create_check_run(
        self,
        owner: str,
        repo: str,
        token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.request_json(
            "POST",
            f"/repos/{_quote(owner)}/{_quote(repo)}/check-runs",
            token=token,
            data=payload,
        )

    def update_check_run(
        self,
        owner: str,
        repo: str,
        check_run_id: str | int,
        token: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self.request_json(
            "PATCH",
            f"/repos/{_quote(owner)}/{_quote(repo)}/check-runs/{check_run_id}",
            token=token,
            data=payload,
        )

    def paginated_get(self, path: str, token: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 11):
            payload = self.request_json(
                "GET",
                f"{path}{separator}per_page=100&page={page}",
                token=token,
            )
            if not isinstance(payload, list) or not payload:
                break
            items.extend(item for item in payload if isinstance(item, dict))
            if len(payload) < 100:
                break
        return items

    def request_json(
        self,
        method: str,
        path: str,
        token: str,
        data: dict[str, Any] | None = None,
    ) -> Any:
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
        request = urllib.request.Request(
            self._url(path),
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=20) as response:
            response_body = response.read().decode("utf-8")
        return json.loads(response_body) if response_body else {}

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.settings.github_api_base_url.rstrip('/')}/{path.lstrip('/')}"

    def _private_key(self) -> str:
        if self.settings.github_app_private_key:
            return normalize_private_key(self.settings.github_app_private_key)
        if self.settings.github_app_private_key_path:
            return normalize_private_key(
                self.settings.github_app_private_key_path.read_text(encoding="utf-8")
            )
        raise RuntimeError("GITHUB_APP_PRIVATE_KEY or GITHUB_APP_PRIVATE_KEY_PATH is required")


@dataclass(frozen=True)
class GitHubWebhookReviewPlan:
    status: str
    reason: str
    requests: list[ReviewRequest]


class GitHubWebhookProcessor:
    pull_request_actions = {"opened", "reopened", "synchronize", "ready_for_review"}

    def __init__(
        self,
        settings: Settings,
        client: GitHubAppClient | None = None,
    ) -> None:
        self.settings = settings
        self.client = client or GitHubAppClient(settings)

    def review_plan(
        self,
        event_name: str,
        delivery_id: str,
        payload: dict[str, Any],
    ) -> GitHubWebhookReviewPlan:
        if event_name == "ping":
            return GitHubWebhookReviewPlan("ignored", "GitHub webhook ping", [])
        if event_name == "pull_request":
            return self._pull_request_plan(delivery_id, payload)
        if event_name == "check_suite":
            return self._check_payload_plan("check_suite", delivery_id, payload)
        if event_name == "check_run":
            return self._check_payload_plan("check_run", delivery_id, payload)
        return GitHubWebhookReviewPlan("ignored", f"unsupported GitHub event: {event_name}", [])

    def _pull_request_plan(
        self,
        delivery_id: str,
        payload: dict[str, Any],
    ) -> GitHubWebhookReviewPlan:
        action = str(payload.get("action", ""))
        if self.settings.github_webhook_review_mode not in {"pull_request", "all"}:
            return GitHubWebhookReviewPlan(
                "accepted",
                "waiting for check_suite or check_run completion",
                [],
            )
        if action not in self.pull_request_actions:
            return GitHubWebhookReviewPlan("ignored", f"unsupported pull_request action: {action}", [])

        pull_request = _dict(payload.get("pull_request"))
        if pull_request.get("draft"):
            return GitHubWebhookReviewPlan("ignored", "draft pull request", [])

        return GitHubWebhookReviewPlan(
            "ready",
            "pull_request event selected for review",
            [self._build_review_request(delivery_id, "pull_request", payload, pull_request)],
        )

    def _check_payload_plan(
        self,
        payload_key: str,
        delivery_id: str,
        payload: dict[str, Any],
    ) -> GitHubWebhookReviewPlan:
        if (
            self.settings.github_webhook_review_mode == "after_checks"
            and payload_key == "check_run"
        ):
            return GitHubWebhookReviewPlan(
                "accepted",
                "waiting for check_suite completion",
                [],
            )
        if self.settings.github_webhook_review_mode not in {"after_checks", "all"}:
            return GitHubWebhookReviewPlan(
                "ignored",
                f"{payload_key} ignored by GITHUB_WEBHOOK_REVIEW_MODE",
                [],
            )
        action = str(payload.get("action", ""))
        if action != "completed":
            return GitHubWebhookReviewPlan("accepted", f"waiting for {payload_key} completion", [])

        check_payload = _dict(payload.get(payload_key))
        if self.settings.github_app_id and (
            _nested_str(check_payload, "app", "id") == str(self.settings.github_app_id)
        ):
            return GitHubWebhookReviewPlan("ignored", "self check event", [])
        if payload_key == "check_run" and check_payload.get("name") == self.settings.github_check_run_name:
            return GitHubWebhookReviewPlan("ignored", "self check_run event", [])

        pull_requests = check_payload.get("pull_requests") or []
        if not isinstance(pull_requests, list) or not pull_requests:
            return GitHubWebhookReviewPlan("ignored", f"{payload_key} has no pull requests", [])

        requests: list[ReviewRequest] = []
        for pull_request_summary in pull_requests:
            pull_number = int(_dict(pull_request_summary).get("number", 0))
            if pull_number <= 0:
                continue
            repository = _repository(payload)
            installation_id = _installation_id(payload)
            token = self.client.installation_token(installation_id)
            pull_request = self.client.get_pull_request(
                repository["owner"],
                repository["name"],
                pull_number,
                token,
            )
            requests.append(
                self._build_review_request(delivery_id, payload_key, payload, pull_request, token)
            )

        if not requests:
            return GitHubWebhookReviewPlan("ignored", f"{payload_key} had no reviewable PR", [])
        return GitHubWebhookReviewPlan("ready", f"{payload_key} completed", requests)

    def _build_review_request(
        self,
        delivery_id: str,
        event_name: str,
        payload: dict[str, Any],
        pull_request: dict[str, Any],
        token: str | None = None,
    ) -> ReviewRequest:
        repository = _repository(payload)
        installation_id = _installation_id(payload)
        resolved_token = token or self.client.installation_token(installation_id)
        pull_number = int(pull_request.get("number", 0))
        head_sha = _nested_str(pull_request, "head", "sha")
        files = self.client.list_pull_files(
            repository["owner"],
            repository["name"],
            pull_number,
            resolved_token,
        )
        checks = self.client.list_check_runs(
            repository["owner"],
            repository["name"],
            head_sha,
            resolved_token,
        )

        return ReviewRequest.from_dict(
            {
                "repository": {
                    "provider": "github",
                    "owner": repository["owner"],
                    "name": repository["name"],
                    "default_branch": repository["default_branch"],
                },
                "pull_request": {
                    "number": pull_number,
                    "title": str(pull_request.get("title", "")),
                    "author": _nested_str(pull_request, "user", "login"),
                    "base_sha": _nested_str(pull_request, "base", "sha"),
                    "head_sha": head_sha,
                    "base_branch": _nested_str(pull_request, "base", "ref"),
                    "head_branch": _nested_str(pull_request, "head", "ref"),
                },
                "checks": [_check_result_payload(check) for check in checks],
                "changed_files": [_changed_file_payload(changed_file) for changed_file in files],
                "github": {
                    "run_id": delivery_id,
                    "delivery_id": delivery_id,
                    "event_name": event_name,
                    "installation_id": installation_id,
                },
            }
        )


def _quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _nested_str(payload: dict[str, Any], *keys: str) -> str:
    value: Any = payload
    for key in keys:
        value = _dict(value).get(key)
    return "" if value is None else str(value)


def _repository(payload: dict[str, Any]) -> dict[str, str]:
    repository = _dict(payload.get("repository"))
    owner_payload = _dict(repository.get("owner"))
    owner = str(owner_payload.get("login") or repository.get("owner") or "")
    name = str(repository.get("name") or "")
    if not owner and repository.get("full_name"):
        owner, _, name = str(repository["full_name"]).partition("/")
    return {
        "owner": owner,
        "name": name,
        "default_branch": str(repository.get("default_branch") or "main"),
    }


def _installation_id(payload: dict[str, Any]) -> str:
    installation_id = _dict(payload.get("installation")).get("id")
    if not installation_id:
        raise RuntimeError("GitHub webhook payload does not include installation.id")
    return str(installation_id)


def _changed_file_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": payload.get("filename", ""),
        "status": payload.get("status", "modified"),
        "additions": payload.get("additions", 0),
        "deletions": payload.get("deletions", 0),
        "patch": payload.get("patch", ""),
    }


def _check_result_payload(payload: dict[str, Any]) -> dict[str, Any]:
    output = _dict(payload.get("output"))
    summary = str(output.get("summary") or output.get("title") or payload.get("html_url") or "")
    return {
        "kind": str(payload.get("name") or "check_run"),
        "status": str(payload.get("status") or "unknown"),
        "conclusion": str(payload.get("conclusion") or "unknown"),
        "summary": summary,
        "log_uri": payload.get("html_url"),
    }
