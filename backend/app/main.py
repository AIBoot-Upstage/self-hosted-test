from __future__ import annotations

from typing import Any

try:
    from fastapi import FastAPI, Header, HTTPException, Request, status
except ModuleNotFoundError as exc:  # pragma: no cover
    raise RuntimeError("FastAPI is not installed. Run `pip install -e .` first.") from exc

from backend.app.core.config import Settings
from backend.app.core.routing import select_route
from backend.app.core.schemas import PullRequestFeatures, ReviewRequest
from backend.app.services.orchestrator import create_orchestrator
from backend.app.services.rag import LocalPolicyIndex
from backend.app.storage.local_store import LocalJsonStore

settings = Settings.from_env()
orchestrator = create_orchestrator(settings)
app = FastAPI(title="AI Code Review Agent", version="0.1.0")


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
    return {"status": "ok", "database": "local-json", "queue": "inline"}


@app.post("/v1/reviews", status_code=status.HTTP_202_ACCEPTED)
async def create_review(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    payload = await request.json()
    review_request = ReviewRequest.from_dict(payload)
    result = orchestrator.run_review(review_request)
    return {
        "review_run_id": result.review_run_id,
        "status": result.status,
        "route_name": result.route.name,
        "model_tier": result.route.model_tier,
        "findings_count": len(result.findings),
        "result": result.to_dict(),
    }


@app.get("/v1/reviews/{review_run_id}")
def get_review(
    review_run_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    store = LocalJsonStore(settings.review_store_path)
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
    route = select_route(features)
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
    result = LocalPolicyIndex(settings.policy_root).sync()
    return {"repository_id": repository_id, "status": "completed", **result}

