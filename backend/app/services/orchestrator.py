from __future__ import annotations

import uuid
from collections.abc import Callable

from backend.app.core.config import Settings
from backend.app.core.schemas import JsonDict, ReviewRequest, ReviewResult
from backend.app.services.llm import LLMClient, create_llm_client
from backend.app.services.publisher import ReviewPublisher, create_publisher
from backend.app.services.rag import LocalPolicyIndex, create_policy_index
from backend.app.services.review_graph import ReviewWorkflowGraph
from backend.app.storage.factory import ReviewStore, create_review_store


class ReviewOrchestrator:
    def __init__(
        self,
        policy_index: LocalPolicyIndex,
        llm_client: LLMClient,
        publisher: ReviewPublisher,
        store: ReviewStore,
    ) -> None:
        self.policy_index = policy_index
        self.llm_client = llm_client
        self.publisher = publisher
        self.store = store

    def run_review(
        self,
        request: ReviewRequest,
        review_run_id: str | None = None,
        event_publisher: Callable[[str, JsonDict | None], object] | None = None,
    ) -> ReviewResult:
        resolved_review_run_id = review_run_id or str(uuid.uuid4())

        def publish(event_type: str, payload: JsonDict | None = None) -> None:
            if event_publisher:
                event_publisher(event_type, payload or {})

        try:
            return ReviewWorkflowGraph(
                policy_index=self.policy_index,
                llm_client=self.llm_client,
                publisher=self.publisher,
                store=self.store,
                event_publisher=publish,
            ).run(
                request=request,
                review_run_id=resolved_review_run_id,
            )
        except Exception as exc:
            publish(
                "review_failed",
                {
                    "error_type": exc.__class__.__name__,
                    "message": str(exc),
                },
            )
            raise


def create_orchestrator(settings: Settings | None = None) -> ReviewOrchestrator:
    resolved_settings = settings or Settings.from_env()
    return ReviewOrchestrator(
        policy_index=create_policy_index(resolved_settings),
        llm_client=create_llm_client(resolved_settings),
        publisher=create_publisher(resolved_settings),
        store=create_review_store(resolved_settings),
    )
