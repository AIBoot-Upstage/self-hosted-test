from __future__ import annotations

import json
import logging
import uuid
from dataclasses import replace
from typing import Any

try:
    from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request, status
    from fastapi.responses import StreamingResponse
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError("FastAPI is not installed. Run `pip install -e .` first.") from exc

from backend.app.core.config import Settings
from backend.app.core.routing import select_route
from backend.app.core.schemas import PullRequestFeatures, ReviewRequest
from backend.app.services.events import InMemoryReviewEventBus
from backend.app.services.github_app import (
    GitHubAppClient,
    GitHubWebhookError,
    GitHubWebhookProcessor,
    verify_github_signature,
)
from backend.app.services.orchestrator import create_orchestrator
from backend.app.services.rag import create_policy_index
from backend.app.storage.factory import create_review_store

settings = Settings.from_env()
orchestrator = create_orchestrator(settings)
review_events = InMemoryReviewEventBus()
app = FastAPI(title="AI Code Review Agent", version="0.1.0")
logger = logging.getLogger(__name__)


def _authorize(authorization: str | None) -> None:
    if not settings.api_token:
        return
    expected = f"Bearer {settings.api_token}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authorization token",
        )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    try:
        create_review_store(settings).healthcheck()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"{settings.storage_backend} database unavailable: {exc}",
        ) from exc
    return {"status": "ok", "database": settings.storage_backend, "queue": "inline"}


def _result_response(result) -> dict[str, Any]:
    return {
        "review_run_id": result.review_run_id,
        "status": result.status,
        "route_name": result.route.name,
        "model_tier": result.route.model_tier,
        "findings_count": len(result.findings),
        "result": result.to_dict(),
    }


def _run_review(review_run_id: str, review_request: ReviewRequest):
    return orchestrator.run_review(
        review_request,
        review_run_id=review_run_id,
        event_publisher=review_events.publisher(review_run_id),
    )


def _run_review_background(review_run_id: str, review_request: ReviewRequest) -> None:
    try:
        _run_review(review_run_id, review_request)
    except Exception:
        logger.exception("review background task failed", extra={"review_run_id": review_run_id})
        return


def _handle_github_webhook_background(
    event_name: str,
    delivery_id: str,
    payload: dict[str, Any],
) -> None:
    try:
        github_client = GitHubAppClient(settings)
        plan = GitHubWebhookProcessor(settings, client=github_client).review_plan(
            event_name,
            delivery_id,
            payload,
        )
        store = create_review_store(settings)
        existing_idempotency_keys = {
            str(record.get("idempotency_key", "")) for record in store.list_reviews()
        }
        for review_request in plan.requests:
            if review_request.idempotency_key() in existing_idempotency_keys:
                logger.info(
                    "github webhook review skipped because idempotency key already exists",
                    extra={
                        "event_name": event_name,
                        "delivery_id": delivery_id,
                        "idempotency_key": review_request.idempotency_key(),
                    },
                )
                continue
            review_request = _create_pending_github_check(github_client, review_request)
            review_run_id = str(uuid.uuid4())
            review_events.publish(
                review_run_id,
                "review_queued",
                {
                    "source": "github_webhook",
                    "event_name": event_name,
                    "delivery_id": delivery_id,
                    "repository": review_request.repository.full_name,
                    "pull_request_number": review_request.pull_request.number,
                    "review_mode": review_request.review_mode,
                },
            )
            try:
                _run_review(review_run_id, review_request)
            except Exception as exc:
                review_events.publish(
                    review_run_id,
                    "review_failed",
                    {
                        "error_type": type(exc).__name__,
                        "message": _exception_summary(exc),
                    },
                )
                _complete_failed_github_check(github_client, review_request, exc)
                logger.exception(
                    "github webhook review failed",
                    extra={
                        "event_name": event_name,
                        "delivery_id": delivery_id,
                        "review_run_id": review_run_id,
                        "repository": review_request.repository.full_name,
                        "pull_request_number": review_request.pull_request.number,
                    },
                )
    except Exception:
        logger.exception(
            "github webhook background task failed",
            extra={"event_name": event_name, "delivery_id": delivery_id},
        )


def _create_pending_github_check(
    github_client: GitHubAppClient,
    review_request: ReviewRequest,
) -> ReviewRequest:
    if not review_request.github.installation_id:
        return review_request
    token = github_client.installation_token(review_request.github.installation_id)
    pending_summary = (
        "사용자 요청으로 심층 리뷰를 실행 중입니다."
        if review_request.review_mode == "deep_quality_review"
        else "AI 코드 리뷰를 실행 중입니다."
    )
    if review_request.github.check_run_id:
        github_client.update_check_run(
            review_request.repository.owner,
            review_request.repository.name,
            review_request.github.check_run_id,
            token,
            {
                "status": "in_progress",
                "started_at": _utc_now_iso(),
                "output": {
                    "title": settings.github_check_run_name,
                    "summary": pending_summary,
                },
            },
        )
        return review_request
    check_run = github_client.create_check_run(
        review_request.repository.owner,
        review_request.repository.name,
        token,
        {
            "name": settings.github_check_run_name,
            "head_sha": review_request.pull_request.head_sha,
            "status": "in_progress",
            "started_at": _utc_now_iso(),
            "output": {
                "title": settings.github_check_run_name,
                "summary": pending_summary,
            },
        },
    )
    return replace(
        review_request,
        github=replace(review_request.github, check_run_id=str(check_run.get("id", ""))),
    )


def _complete_failed_github_check(
    github_client: GitHubAppClient,
    review_request: ReviewRequest,
    exc: Exception,
) -> None:
    if not review_request.github.installation_id or not review_request.github.check_run_id:
        return
    try:
        token = github_client.installation_token(review_request.github.installation_id)
        github_client.update_check_run(
            review_request.repository.owner,
            review_request.repository.name,
            review_request.github.check_run_id,
            token,
            {
                "status": "completed",
                "conclusion": "failure",
                "completed_at": _utc_now_iso(),
                "output": {
                    "title": f"{settings.github_check_run_name} 실패",
                    "summary": (
                        "AI 코드 리뷰 실행 중 오류가 발생했습니다.\n\n"
                        f"- 오류 유형: `{type(exc).__name__}`\n"
                        f"- 오류 내용: {_exception_summary(exc)}"
                    ),
                },
            },
        )
    except Exception:
        logger.exception(
            "failed to update github check run after review failure",
            extra={
                "repository": review_request.repository.full_name,
                "pull_request_number": review_request.pull_request.number,
                "check_run_id": review_request.github.check_run_id,
            },
        )


def _exception_summary(exc: Exception) -> str:
    message = str(exc).replace("\n", " ").strip()
    if not message:
        return type(exc).__name__
    return message[:800]


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@app.post("/v1/reviews", status_code=status.HTTP_202_ACCEPTED)
async def create_review(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None),
    wait: bool = Query(default=False),
) -> dict[str, Any]:
    _authorize(authorization)
    payload = await request.json()
    review_request = ReviewRequest.from_dict(payload)
    review_run_id = str(uuid.uuid4())
    review_events.publish(
        review_run_id,
        "review_queued",
        {
            "repository": review_request.repository.full_name,
            "pull_request_number": review_request.pull_request.number,
            "review_mode": review_request.review_mode,
        },
    )
    if wait:
        return _result_response(_run_review(review_run_id, review_request))

    background_tasks.add_task(_run_review_background, review_run_id, review_request)
    return {
        "review_run_id": review_run_id,
        "status": "accepted",
        "events_url": f"/v1/reviews/{review_run_id}/events",
        "result_url": f"/v1/reviews/{review_run_id}",
    }


@app.post("/v1/github/webhooks", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str | None = Header(default=None, alias="X-GitHub-Event"),
    x_github_delivery: str | None = Header(default=None, alias="X-GitHub-Delivery"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    payload_body = await request.body()
    try:
        verify_github_signature(
            payload_body,
            settings.github_webhook_secret,
            x_hub_signature_256,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GitHubWebhookError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    try:
        payload = json.loads(payload_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="payload must be object")
    if not x_github_event:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="X-GitHub-Event is required")
    if not x_github_delivery:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-GitHub-Delivery is required",
        )

    background_tasks.add_task(
        _handle_github_webhook_background,
        x_github_event,
        x_github_delivery,
        payload,
    )
    return {
        "status": "accepted",
        "event_name": x_github_event,
        "delivery_id": x_github_delivery,
        "review_mode": settings.github_webhook_review_mode,
    }


@app.get("/v1/reviews/{review_run_id}/events")
async def stream_review_events(
    review_run_id: str,
    authorization: str | None = Header(default=None),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    _authorize(authorization)
    if not review_events.has_run(review_run_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="review events not found")
    try:
        after_sequence = int(last_event_id or "0")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Last-Event-ID must be an integer",
        ) from exc

    async def event_generator():
        async for chunk in review_events.stream(review_run_id, after_sequence=after_sequence):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/v1/reviews")
def list_reviews(
    authorization: str | None = Header(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    route_name: str | None = Query(default=None),
    model_tier: str | None = Query(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    store = create_review_store(settings)
    records = store.list_reviews(limit=limit, route_name=route_name, model_tier=model_tier)
    return {"count": len(records), "reviews": records}


@app.get("/v1/reviews/{review_run_id}")
def get_review(
    review_run_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    store = create_review_store(settings)
    record = store.get_review(review_run_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="review not found")
    return record


@app.post("/v1/routing/preview")
async def routing_preview(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    payload = await request.json()
    features = PullRequestFeatures(
        syntax_status=str(payload.get("syntax_status", "unknown")),
        lint_status=str(payload.get("lint_status", "unknown")),
        test_status=str(payload.get("test_status", "unknown")),
        changed_files_count=int(payload.get("changed_files_count", 0)),
        changed_lines=int(payload.get("changed_lines", 0)),
        risk_files=list(payload.get("risk_files", [])),
        policy_available=bool(payload.get("policy_available", False)),
        router_confidence=float(payload.get("router_confidence", 0.8)),
    )
    route = select_route(features, review_mode=str(payload.get("review_mode", "auto")))
    return {
        "route_name": route.name,
        "model_tier": route.model_tier,
        "use_rag": route.use_rag,
        "router_confidence": route.confidence,
        "reasons": route.reasons,
    }


@app.post("/v1/repositories/{repository_id}/policies/sync")
def sync_policies(
    repository_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    result = create_policy_index(settings).sync()
    return {"repository_id": repository_id, "status": "completed", **result}
