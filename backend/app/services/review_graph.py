from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any, TypedDict

from backend.app.core.routing import extract_features, select_route
from backend.app.core.schemas import (
    ComplexityMetric,
    FileChangeSummary,
    JsonDict,
    ModelCallUsage,
    PolicyChunk,
    PullRequestFeatures,
    ReviewHarnessContext,
    ReviewFinding,
    ReviewRequest,
    ReviewResult,
    ReviewRoute,
    ReviewSummary,
)
from backend.app.services.llm import LLMClient
from backend.app.services.policy_harness import PolicyHarness
from backend.app.services.prompt_builder import ReviewPromptBatch, build_review_prompt_batches
from backend.app.services.publisher import ReviewPublisher
from backend.app.services.rag import LocalPolicyIndex
from backend.app.services.rag import rank_policy_chunks
from backend.app.services.review_quality import validate_and_rank_findings
from backend.app.storage.factory import ReviewStore
from review_harness.scripts.complexity_metrics import analyze_complexity

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
    complexity_metrics: list[ComplexityMetric]
    policies: list[PolicyChunk]
    review_harness: ReviewHarnessContext
    prompt_batches: list[ReviewPromptBatch]
    summary: ReviewSummary
    findings: list[ReviewFinding]
    finding_validation: JsonDict
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
        policy_harness: PolicyHarness,
        event_publisher: Callable[[str, JsonDict | None], object] | None = None,
    ) -> None:
        self.policy_index = policy_index
        self.llm_client = llm_client
        self.publisher = publisher
        self.store = store
        self.policy_harness = policy_harness
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
        graph.add_node("analyze_complexity", self._analyze_complexity)
        graph.add_node("select_harness", self._select_harness)
        graph.add_node("retrieve_policies", self._retrieve_policies)
        graph.add_node("skip_policy_retrieval", self._skip_policy_retrieval)
        graph.add_node("build_prompt", self._build_prompt)
        graph.add_node("call_llm", self._call_llm)
        graph.add_node("validate_findings", self._validate_findings)
        graph.add_node("assemble_result", self._assemble_result)
        graph.add_node("persist_result", self._persist_result)
        graph.add_node("publish_comment", self._publish_comment)
        graph.add_node("complete_review", self._complete_review)

        graph.add_edge(START, "create_review")
        graph.add_edge("create_review", "extract_features")
        graph.add_edge("extract_features", "select_route")
        graph.add_edge("select_route", "analyze_complexity")
        graph.add_edge("analyze_complexity", "select_harness")
        graph.add_conditional_edges(
            "select_harness",
            self._policy_retrieval_path,
            {
                "retrieve": "retrieve_policies",
                "skip": "skip_policy_retrieval",
            },
        )
        graph.add_edge("retrieve_policies", "build_prompt")
        graph.add_edge("skip_policy_retrieval", "build_prompt")
        graph.add_edge("build_prompt", "call_llm")
        graph.add_edge("call_llm", "validate_findings")
        graph.add_edge("validate_findings", "assemble_result")
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
        policy_available = bool(request.repository_policies) or self.policy_index.has_policy()
        features = extract_features(request, policy_available=policy_available)
        self._publish("features_extracted", features.to_dict())
        return {"policy_available": policy_available, "features": features}

    def _select_route(self, state: ReviewWorkflowState) -> JsonDict:
        route = select_route(state["features"], review_mode=state["request"].review_mode)
        self._publish("route_selected", route.to_dict())
        return {"route": route}

    def _analyze_complexity(self, state: ReviewWorkflowState) -> JsonDict:
        request = state["request"]
        metrics = analyze_complexity(request)
        request = replace(request, complexity_metrics=metrics)
        self._publish(
            "complexity_analyzed",
            {
                "tool": "radon",
                "metric": "cyclomatic_complexity",
                "measured_count": len(metrics),
                "threshold_exceeded_count": sum(
                    metric.exceeded_threshold for metric in metrics
                ),
                "files": sorted({metric.file_path for metric in metrics}),
            },
        )
        return {"request": request, "complexity_metrics": metrics}

    def _policy_retrieval_path(self, state: ReviewWorkflowState) -> str:
        return "retrieve" if state["route"].use_rag else "skip"

    def _select_harness(self, state: ReviewWorkflowState) -> JsonDict:
        context = self.policy_harness.select(state["request"], state["route"])
        self._publish(
            "review_harness_selected",
            {
                "version": context.version,
                "signals": sorted(context.signals),
                "skills": [skill.skill_id for skill in context.skills],
                "knowledge_cards": [card.card_id for card in context.knowledge_cards],
                "policy_types": context.policy_types,
                "candidate_policy_types": context.candidate_policy_types,
            },
        )
        return {"review_harness": context}

    def _retrieve_policies(self, state: ReviewWorkflowState) -> JsonDict:
        context = state["review_harness"]
        self._publish(
            "policy_retrieval_started",
            {"candidate_top_k": 8, "policy_types": context.candidate_policy_types},
        )
        indexed_policies = self.policy_index.search(
            state["request"],
            top_k=8,
            policy_types=set(context.candidate_policy_types) or None,
        )
        policies = rank_policy_chunks(
            [*state["request"].repository_policies, *indexed_policies],
            state["request"],
            top_k=8,
            policy_types=set(context.candidate_policy_types) or None,
        )
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
        batches = build_review_prompt_batches(
            state["request"],
            state["route"],
            state["policies"],
            policy_harness=self.policy_harness,
        )
        selected_policies: dict[tuple[str, str], PolicyChunk] = {}
        selected_skills = {}
        selected_knowledge_cards = {}
        for batch in batches:
            for policy in batch.policies:
                selected_policies[(policy.source_path, policy.section_title)] = policy
            if batch.harness:
                for skill in batch.harness.skills:
                    selected_skills[skill.skill_id] = skill
                for card in batch.harness.knowledge_cards:
                    selected_knowledge_cards[card.card_id] = card
        harness = state["review_harness"]
        applied_harness = ReviewHarnessContext(
            version=harness.version,
            signals=harness.signals,
            skills=list(selected_skills.values()) or harness.skills,
            knowledge_cards=(
                list(selected_knowledge_cards.values()) or harness.knowledge_cards
            ),
            policy_types=sorted(
                {
                    policy_type
                    for skill in (list(selected_skills.values()) or harness.skills)
                    for policy_type in skill.policy_types
                }
            ),
            candidate_policy_types=harness.candidate_policy_types,
        )
        self._publish(
            "prompt_built",
            {
                "batch_count": len(batches),
                "selected_files": sum(len(batch.request.changed_files) for batch in batches),
                "patch_chars": sum(batch.patch_chars for batch in batches),
                "skills": [skill.skill_id for skill in applied_harness.skills],
                "knowledge_cards": [
                    card.card_id for card in applied_harness.knowledge_cards
                ],
                "policies_per_batch": [len(batch.policies) for batch in batches],
            },
        )
        return {
            "prompt_batches": batches,
            "policies": list(selected_policies.values()),
            "review_harness": applied_harness,
        }

    def _call_llm(self, state: ReviewWorkflowState) -> JsonDict:
        route = state["route"]
        batches = state["prompt_batches"]
        self._publish(
            "llm_call_started",
            {
                "model_tier": route.model_tier,
                "route_name": route.name,
                "batch_count": len(batches),
            },
        )
        for batch in batches:
            self._publish(
                "llm_batch_started",
                {
                    "batch_index": batch.index,
                    "batch_count": batch.count,
                    "files_count": len(batch.request.changed_files),
                    "patch_chars": batch.patch_chars,
                    "skills": (
                        [skill.skill_id for skill in batch.harness.skills]
                        if batch.harness
                        else []
                    ),
                    "knowledge_cards": (
                        [card.card_id for card in batch.harness.knowledge_cards]
                        if batch.harness
                        else []
                    ),
                    "policy_sources": [
                        f"{policy.source_path}#{policy.section_title}"
                        for policy in batch.policies
                    ],
                },
            )

        def generate(batch: ReviewPromptBatch):
            return self.llm_client.generate_review(
                request=batch.request,
                route=route,
                policies=batch.policies,
                messages=batch.messages,
                review_run_id=state["review_run_id"],
                batch_index=batch.index,
                batch_count=batch.count,
            )

        started = time.perf_counter()
        if len(batches) == 1:
            batch_results = [generate(batches[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(4, len(batches))) as executor:
                batch_results = list(executor.map(generate, batches))
        latency_ms = int((time.perf_counter() - started) * 1000)

        summaries = [result[0] for result in batch_results]
        findings = [finding for result in batch_results for finding in result[1]]
        usages = [result[2] for result in batch_results]
        risk_order = {"low": 0, "medium": 1, "high": 2}
        representative = max(
            summaries,
            key=lambda summary: risk_order.get(summary.overall_risk.lower(), 1),
        )
        if len(batches) == 1:
            summary = representative
        else:
            file_summaries_by_path: dict[str, FileChangeSummary] = {}
            for batch_summary in summaries:
                for file_summary in batch_summary.file_summaries:
                    file_summaries_by_path.setdefault(file_summary.file_path, file_summary)
            merged_change_summaries = list(
                dict.fromkeys(
                    batch_summary.change_summary.strip()
                    for batch_summary in summaries
                    if batch_summary.change_summary.strip()
                )
            )
            change_summary = " ".join(merged_change_summaries)
            if len(change_summary) > 1600:
                change_summary = change_summary[:1597].rstrip() + "..."
            summary = ReviewSummary(
                route_name=route.name,
                model_tier=route.model_tier,
                overall_risk=representative.overall_risk,
                short_comment=f"변경 파일 {len(file_summaries_by_path)}개를 검토했습니다.",
                change_summary=change_summary,
                file_summaries=list(file_summaries_by_path.values()),
            )
        usage = ModelCallUsage(
            provider=usages[0].provider,
            model=usages[0].model,
            prompt_tokens=sum(item.prompt_tokens for item in usages),
            completion_tokens=sum(item.completion_tokens for item in usages),
            latency_ms=latency_ms,
            status="completed",
            reasoning_effort=usages[0].reasoning_effort,
            cost_usd=sum(item.cost_usd for item in usages),
            batch_count=len(batches),
        )
        for batch, (_, batch_findings, batch_usage) in zip(batches, batch_results, strict=True):
            self._publish(
                "llm_batch_completed",
                {
                    "batch_index": batch.index,
                    "batch_count": batch.count,
                    "findings_count": len(batch_findings),
                    "prompt_tokens": batch_usage.prompt_tokens,
                    "completion_tokens": batch_usage.completion_tokens,
                    "latency_ms": batch_usage.latency_ms,
                },
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
                "batch_count": usage.batch_count,
            },
        )
        return {"summary": summary, "findings": findings, "usage": usage}

    def _validate_findings(self, state: ReviewWorkflowState) -> JsonDict:
        findings, report = validate_and_rank_findings(
            request=state["request"],
            route=state["route"],
            policies=state["policies"],
            findings=state["findings"],
            knowledge_cards=state["review_harness"].knowledge_cards,
        )
        self._publish("findings_validated", report)
        return {"findings": findings, "finding_validation": report}

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
            complexity_metrics=state.get("complexity_metrics", []),
            review_harness=state["review_harness"],
            finding_validation=state["finding_validation"],
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
