from __future__ import annotations

import uuid

from backend.app.core.config import Settings
from backend.app.core.routing import extract_features, select_route
from backend.app.core.schemas import ReviewRequest, ReviewResult
from backend.app.services.llm import LLMClient, create_llm_client
from backend.app.services.prompt_builder import build_review_messages
from backend.app.services.publisher import ReviewPublisher, create_publisher
from backend.app.services.rag import LocalPolicyIndex, create_policy_index
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

    def run_review(self, request: ReviewRequest) -> ReviewResult:
        policy_available = self.policy_index.has_policy()
        features = extract_features(request, policy_available=policy_available)
        route = select_route(features)
        policies = self.policy_index.search(request) if route.use_rag else []
        messages = build_review_messages(request, route, policies)
        summary, findings, usage = self.llm_client.generate_review(
            request=request,
            route=route,
            policies=policies,
            messages=messages,
        )
        result = ReviewResult(
            review_run_id=str(uuid.uuid4()),
            status="completed",
            idempotency_key=request.idempotency_key(),
            summary=summary,
            findings=findings,
            route=route,
            features=features,
            model_call=usage,
            retrieved_policies=policies,
        )
        self.store.save_review(result)
        self.publisher.publish(request, result)
        return result


def create_orchestrator(settings: Settings | None = None) -> ReviewOrchestrator:
    resolved_settings = settings or Settings.from_env()
    return ReviewOrchestrator(
        policy_index=create_policy_index(resolved_settings),
        llm_client=create_llm_client(resolved_settings),
        publisher=create_publisher(resolved_settings),
        store=create_review_store(resolved_settings),
    )
