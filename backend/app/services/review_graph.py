from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypedDict

from backend.app.core.routing import extract_features, select_route
from backend.app.core.schemas import (
    JsonDict,
    ModelCallUsage,
    PolicyChunk,
    PullRequestFeatures,
    ReviewFinding,
    ReviewRequest,
    ReviewResult,
    ReviewRoute,
    ReviewSummary,
)
from backend.app.services.llm import LLMClient
from backend.app.services.prompt_builder import build_review_messages
from backend.app.services.publisher import ReviewPublisher
from backend.app.services.rag import LocalPolicyIndex
from backend.app.storage.factory import ReviewStore

try:
    from langgraph.graph import END, START, StateGraph

    LANGGRAPH_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover - 배포 환경에서는 pyproject 의존성으로 설치된다.
    END = "__end__"
    START = "__start__"
    LANGGRAPH_AVAILABLE = False

    class StateGraph:  # type: ignore[no-redef]
        def __init__(self, state_schema: type[TypedDict]) -> None:
            self.nodes: dict[str, Callable[[dict[str, Any]], dict[str, Any] | None]] = {}
            self.edges: dict[str, list[str]] = {}
            self.conditional_edges: dict[
                str,
                tuple[Callable[[dict[str, Any]], str], dict[str, str]],
            ] = {}

        def add_node(self, name: str, node: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
            self.nodes[name] = node

        def add_edge(self, source: str, target: str) -> None:
            self.edges.setdefault(source, []).append(target)

        def add_conditional_edges(
            self,
            source: str,
            path: Callable[[dict[str, Any]], str],
            path_map: list[str] | dict[str, str],
        ) -> None:
            if isinstance(path_map, list):
                resolved_path_map = {item: item for item in path_map}
            else:
                resolved_path_map = path_map
            self.conditional_edges[source] = (path, resolved_path_map)

        def compile(self) -> "_LocalCompiledGraph":
            return _LocalCompiledGraph(self.nodes, self.edges, self.conditional_edges)


    class _LocalCompiledGraph:
        def __init__(
            self,
            nodes: dict[str, Callable[[dict[str, Any]], dict[str, Any] | None]],
            edges: dict[str, list[str]],
            conditional_edges: dict[
                str,
                tuple[Callable[[dict[str, Any]], str], dict[str, str]],
            ],
        ) -> None:
            self.nodes = nodes
            self.edges = edges
            self.conditional_edges = conditional_edges

        def invoke(self, input_state: dict[str, Any]) -> dict[str, Any]:
            state = dict(input_state)
            next_nodes = list(self.edges.get(START, []))
            while next_nodes:
                node_name = next_nodes.pop(0)
                if node_name == END:
                    continue
                update = self.nodes[node_name](state) or {}
                state.update(update)
                if node_name in self.conditional_edges:
                    path, path_map = self.conditional_edges[node_name]
                    target = path_map[path(state)]
                    next_nodes = [] if target == END else [target]
                else:
                    next_nodes = [target for target in self.edges.get(node_name, []) if target != END]
            return state


class ReviewWorkflowState(TypedDict, total=False):
    review_run_id: str
    request: ReviewRequest
    policy_available: bool
    features: PullRequestFeatures
    route: ReviewRoute
    policies: list[PolicyChunk]
    messages: list[dict[str, str]]
    summary: ReviewSummary
    findings: list[ReviewFinding]
    usage: ModelCallUsage
    result: ReviewResult
    publish_result: dict[str, object]


def langgraph_runtime_name() -> str:
    return "langgraph" if LANGGRAPH_AVAILABLE else "local_fallback"


class ReviewWorkflowGraph:
    def __init__(
        self,
        policy_index: LocalPolicyIndex,
        llm_client: LLMClient,
        publisher: ReviewPublisher,
        store: ReviewStore,
        event_publisher: Callable[[str, JsonDict | None], object] | None = None,
    ) -> None:
        self.policy_index = policy_index
        self.llm_client = llm_client
        self.publisher = publisher
        self.store = store
        self.event_publisher = event_publisher
        self.graph = self._build_graph()

    def run(self, request: ReviewRequest, review_run_id: str) -> ReviewResult:
        final_state = self.graph.invoke(
            {
                "review_run_id": review_run_id,
                "request": request,
            }
        )
        return final_state["result"]

    def _build_graph(self):
        graph = StateGraph(ReviewWorkflowState)
        graph.add_node("create_review", self._create_review)
        graph.add_node("extract_features", self._extract_features)
        graph.add_node("select_route", self._select_route)
        graph.add_node("retrieve_policies", self._retrieve_policies)
        graph.add_node("skip_policy_retrieval", self._skip_policy_retrieval)
        graph.add_node("build_prompt", self._build_prompt)
        graph.add_node("call_llm", self._call_llm)
        graph.add_node("assemble_result", self._assemble_result)
        graph.add_node("persist_result", self._persist_result)
        graph.add_node("publish_comment", self._publish_comment)
        graph.add_node("complete_review", self._complete_review)

        graph.add_edge(START, "create_review")
        graph.add_edge("create_review", "extract_features")
        graph.add_edge("extract_features", "select_route")
        graph.add_conditional_edges(
            "select_route",
            self._policy_retrieval_path,
            {
                "retrieve": "retrieve_policies",
                "skip": "skip_policy_retrieval",
            },
        )
        graph.add_edge("retrieve_policies", "build_prompt")
        graph.add_edge("skip_policy_retrieval", "build_prompt")
        graph.add_edge("build_prompt", "call_llm")
        graph.add_edge("call_llm", "assemble_result")
        graph.add_edge("assemble_result", "persist_result")
        graph.add_edge("persist_result", "publish_comment")
        graph.add_edge("publish_comment", "complete_review")
        graph.add_edge("complete_review", END)
        return graph.compile()

    def _publish(self, event_type: str, payload: JsonDict | None = None) -> None:
        if self.event_publisher:
            self.event_publisher(event_type, payload or {})

    def _create_review(self, state: ReviewWorkflowState) -> JsonDict:
        request = state["request"]
        self._publish(
            "review_created",
            {
                "repository": request.repository.full_name,
                "pull_request_number": request.pull_request.number,
                "head_sha": request.pull_request.head_sha,
                "workflow_engine": langgraph_runtime_name(),
            },
        )
        return {}

    def _extract_features(self, state: ReviewWorkflowState) -> JsonDict:
        request = state["request"]
        policy_available = self.policy_index.has_policy()
        features = extract_features(request, policy_available=policy_available)
        self._publish("features_extracted", features.to_dict())
        return {"policy_available": policy_available, "features": features}

    def _select_route(self, state: ReviewWorkflowState) -> JsonDict:
        route = select_route(state["features"])
        self._publish("route_selected", route.to_dict())
        return {"route": route}

    def _policy_retrieval_path(self, state: ReviewWorkflowState) -> str:
        return "retrieve" if state["route"].use_rag else "skip"

    def _retrieve_policies(self, state: ReviewWorkflowState) -> JsonDict:
        self._publish("policy_retrieval_started", {"top_k": 5})
        policies = self.policy_index.search(state["request"])
        self._publish(
            "policy_retrieval_completed",
            {
                "retrieved_count": len(policies),
                "sources": [policy.source_path for policy in policies],
            },
        )
        return {"policies": policies}

    def _skip_policy_retrieval(self, state: ReviewWorkflowState) -> JsonDict:
        self._publish("policy_retrieval_skipped", {"reason": "route does not require rag"})
        return {"policies": []}

    def _build_prompt(self, state: ReviewWorkflowState) -> JsonDict:
        messages = build_review_messages(state["request"], state["route"], state["policies"])
        self._publish("prompt_built", {"messages_count": len(messages)})
        return {"messages": messages}

    def _call_llm(self, state: ReviewWorkflowState) -> JsonDict:
        route = state["route"]
        self._publish(
            "llm_call_started",
            {
                "model_tier": route.model_tier,
                "route_name": route.name,
            },
        )
        summary, findings, usage = self.llm_client.generate_review(
            request=state["request"],
            route=route,
            policies=state["policies"],
            messages=state["messages"],
            review_run_id=state["review_run_id"],
        )
        self._publish(
            "llm_call_completed",
            {
                "provider": usage.provider,
                "model": usage.model,
                "reasoning_effort": usage.reasoning_effort,
                "latency_ms": usage.latency_ms,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
            },
        )
        return {"summary": summary, "findings": findings, "usage": usage}

    def _assemble_result(self, state: ReviewWorkflowState) -> JsonDict:
        request = state["request"]
        result = ReviewResult(
            review_run_id=state["review_run_id"],
            status="completed",
            idempotency_key=request.idempotency_key(),
            summary=state["summary"],
            findings=state["findings"],
            route=state["route"],
            features=state["features"],
            model_call=state["usage"],
            retrieved_policies=state["policies"],
        )
        return {"result": result}

    def _persist_result(self, state: ReviewWorkflowState) -> JsonDict:
        self.store.save_review(state["result"])
        self._publish("review_persisted", {"storage": self.store.__class__.__name__})
        return {}

    def _publish_comment(self, state: ReviewWorkflowState) -> JsonDict:
        publish_result = self.publisher.publish(state["request"], state["result"])
        self._publish("comment_published", publish_result)
        return {"publish_result": publish_result}

    def _complete_review(self, state: ReviewWorkflowState) -> JsonDict:
        result = state["result"]
        self._publish(
            "review_completed",
            {
                "status": result.status,
                "route_name": result.route.name,
                "model_tier": result.route.model_tier,
                "findings_count": len(result.findings),
                "workflow_engine": langgraph_runtime_name(),
            },
        )
        return {}
