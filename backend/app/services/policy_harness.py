from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from backend.app.core.schemas import (
    ReviewHarnessContext,
    ReviewKnowledgeCard,
    ReviewRequest,
    ReviewRoute,
    ReviewSkill,
)
from review_harness.scripts.diff_signals import analyze_diff, reviewable_patch_text


class PolicyHarness:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.manifest = self._load_manifest()
        self.source_ids = self._load_source_ids()
        self.design_source_ids = self._load_design_source_ids()
        self._validate_skills()
        self.knowledge_cards = self._load_knowledge_cards()

    def _load_manifest(self) -> dict[str, Any]:
        manifest_path = self.root / "manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError(f"review harness manifest is missing: {manifest_path}")
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("skills"), list):
            raise RuntimeError("review harness manifest must contain a skills list")
        return payload

    def _skill_instructions(self, relative_path: str) -> str:
        root = self.root.resolve()
        path = (root / relative_path).resolve()
        if root not in path.parents or not path.is_file():
            raise RuntimeError(f"invalid review skill path: {relative_path}")
        return path.read_text(encoding="utf-8").strip()[:2400]

    def _reference_payload(self, manifest_key: str) -> dict[str, Any]:
        relative_path = str(self.manifest.get(manifest_key) or "")
        root = self.root.resolve()
        path = (root / relative_path).resolve()
        if not relative_path or root not in path.parents or not path.is_file():
            raise RuntimeError(f"invalid review harness reference: {relative_path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError(f"review harness reference must be an object: {relative_path}")
        return payload

    def _load_source_ids(self) -> set[str]:
        payload = self._reference_payload("sources_path")
        sources = payload.get("sources")
        if not isinstance(sources, list):
            raise RuntimeError("review harness sources must contain a sources list")
        source_ids: set[str] = set()
        for source in sources:
            if not isinstance(source, dict):
                raise RuntimeError("review harness source must be an object")
            source_id = str(source.get("id") or "")
            url = str(source.get("url") or "")
            if not source_id or not source.get("authority") or not source.get("title"):
                raise RuntimeError("review harness source requires id, authority, and title")
            if not url.startswith("https://"):
                raise RuntimeError(f"review harness source must use HTTPS: {source_id}")
            if source_id in source_ids:
                raise RuntimeError(f"duplicate review harness source ID: {source_id}")
            source_ids.add(source_id)
        return source_ids

    def _validate_skills(self) -> None:
        skill_ids: set[str] = set()
        for item in self.manifest["skills"]:
            if not isinstance(item, dict):
                raise RuntimeError("review harness skill must be an object")
            skill_id = str(item.get("id") or "")
            source_ids = {str(value) for value in item.get("source_ids", [])}
            if not skill_id or skill_id in skill_ids:
                raise RuntimeError(f"invalid or duplicate review harness skill ID: {skill_id}")
            if not source_ids:
                raise RuntimeError(f"review harness skill requires source IDs: {skill_id}")
            unknown_sources = source_ids - self.source_ids
            if unknown_sources:
                raise RuntimeError(
                    f"unknown review harness source IDs for {skill_id}: "
                    f"{sorted(unknown_sources)}"
                )
            self._skill_instructions(str(item.get("path") or ""))
            skill_ids.add(skill_id)

    def _load_design_source_ids(self) -> set[str]:
        source_ids = {str(value) for value in self.manifest.get("design_source_ids", [])}
        unknown_sources = source_ids - self.source_ids
        if unknown_sources:
            raise RuntimeError(
                f"unknown review harness design source IDs: {sorted(unknown_sources)}"
            )
        return source_ids

    def _load_knowledge_cards(self) -> list[dict[str, Any]]:
        payload = self._reference_payload("knowledge_cards_path")
        cards = payload.get("cards")
        if not isinstance(cards, list):
            raise RuntimeError("review harness knowledge cards must contain a cards list")
        known_skills = {str(item["id"]) for item in self.manifest["skills"]}
        card_ids: set[str] = set()
        required_fields = {
            "id",
            "title",
            "skill_id",
            "check",
            "evidence_required",
            "false_positive_guard",
            "severity_cap",
        }
        for card in cards:
            if not isinstance(card, dict):
                raise RuntimeError("review harness knowledge card must be an object")
            missing_fields = [field for field in required_fields if not card.get(field)]
            if missing_fields:
                raise RuntimeError(
                    f"review harness knowledge card is incomplete: {sorted(missing_fields)}"
                )
            card_id = str(card["id"])
            if card_id in card_ids:
                raise RuntimeError(f"duplicate review harness knowledge card ID: {card_id}")
            if str(card["skill_id"]) not in known_skills:
                raise RuntimeError(f"unknown skill for review knowledge card: {card_id}")
            source_ids = {str(source_id) for source_id in card.get("source_ids", [])}
            if not source_ids:
                raise RuntimeError(f"review knowledge card requires source IDs: {card_id}")
            unknown_sources = source_ids - self.source_ids
            if unknown_sources:
                raise RuntimeError(
                    f"unknown review harness source IDs: {sorted(unknown_sources)}"
                )
            card_ids.add(card_id)
        return cards

    @staticmethod
    def _contains_marker(text: str, marker: str) -> bool:
        if re.fullmatch(r"[a-z0-9_]+", marker):
            return re.search(rf"(?<![a-z0-9_]){re.escape(marker)}(?![a-z0-9_])", text) is not None
        return marker in text

    def select(self, request: ReviewRequest, route: ReviewRoute) -> ReviewHarnessContext:
        signals = analyze_diff(request)
        selected: list[ReviewSkill] = []
        for item in self.manifest["skills"]:
            routes = {str(value) for value in item.get("routes", [])}
            if route.name not in routes:
                continue
            required_signals = {str(value) for value in item.get("signals", [])}
            matched_signals = required_signals & signals.keys()
            always_routes = {str(value) for value in item.get("always_routes", [])}
            always_for_route = bool(item.get("always", False)) or route.name in always_routes
            if not always_for_route and not matched_signals:
                continue
            score = (
                (100 if always_for_route else 0)
                + int(item.get("priority", 0))
                + (20 * len(matched_signals))
            )
            selected.append(
                ReviewSkill(
                    skill_id=str(item["id"]),
                    title=str(item.get("title") or item["id"]),
                    instructions=self._skill_instructions(str(item["path"])),
                    policy_types=[str(value) for value in item.get("policy_types", [])],
                    source_ids=[str(value) for value in item.get("source_ids", [])],
                    score=score,
                )
            )

        limits = self.manifest.get("max_skills", {})
        max_skills = int(limits.get(route.name, 3))
        ranked = sorted(selected, key=lambda skill: (-skill.score, skill.skill_id))
        candidate_policy_types = sorted(
            {policy_type for skill in ranked for policy_type in skill.policy_types}
        )
        selected = ranked[:max_skills]
        policy_types = sorted(
            {policy_type for skill in selected for policy_type in skill.policy_types}
        )
        selected_skill_ids = {skill.skill_id for skill in selected}
        changed_paths = "\n".join(file.path.lower() for file in request.changed_files)
        changed_patches = "\n".join(
            reviewable_patch_text(file.patch).lower() for file in request.changed_files
        )
        cards: list[ReviewKnowledgeCard] = []
        for item in self.knowledge_cards:
            skill_id = str(item.get("skill_id") or "")
            if skill_id not in selected_skill_ids:
                continue
            routes = {str(value) for value in item.get("routes", [])}
            if routes and route.name not in routes:
                continue
            required_signals = {str(value) for value in item.get("signals", [])}
            matched_signals = required_signals & signals.keys()
            always = bool(item.get("always", False))
            if required_signals and not matched_signals and not always:
                continue
            path_markers = [str(value).lower() for value in item.get("path_markers", [])]
            patch_markers = [str(value).lower() for value in item.get("patch_markers", [])]
            matched_path = next(
                (
                    marker
                    for marker in path_markers
                    if self._contains_marker(changed_paths, marker)
                ),
                None,
            )
            matched_patch = next(
                (
                    marker
                    for marker in patch_markers
                    if self._contains_marker(changed_patches, marker)
                ),
                None,
            )
            if item.get("require_patch") and not matched_patch:
                continue
            if item.get("require_path_and_patch") and not (matched_path and matched_patch):
                continue
            if (path_markers or patch_markers) and not (matched_path or matched_patch or always):
                continue
            cards.append(
                ReviewKnowledgeCard(
                    card_id=str(item["id"]),
                    title=str(item.get("title") or item["id"]),
                    skill_id=skill_id,
                    check=str(item.get("check") or ""),
                    evidence_required=str(item.get("evidence_required") or ""),
                    false_positive_guard=str(item.get("false_positive_guard") or ""),
                    severity_cap=str(item.get("severity_cap") or "medium"),
                    source_ids=[str(value) for value in item.get("source_ids", [])],
                    forbidden_claim_markers=[
                        str(value).lower()
                        for value in item.get("forbidden_claim_markers", [])
                    ],
                    score=(
                        (20 * len(matched_signals))
                        + (10 if matched_path else 0)
                        + (10 if matched_patch else 0)
                        + (5 if always else 0)
                    ),
                )
            )
        max_cards = int(
            self.manifest.get("max_knowledge_cards", {}).get(route.name, 5)
        )
        ranked_cards = sorted(cards, key=lambda card: (-card.score, card.card_id))
        selected_cards: list[ReviewKnowledgeCard] = []
        selected_card_ids: set[str] = set()
        for skill in selected:
            best_for_skill = next(
                (card for card in ranked_cards if card.skill_id == skill.skill_id),
                None,
            )
            if best_for_skill is None or best_for_skill.card_id in selected_card_ids:
                continue
            selected_cards.append(best_for_skill)
            selected_card_ids.add(best_for_skill.card_id)
            if len(selected_cards) >= max_cards:
                break
        for card in ranked_cards:
            if len(selected_cards) >= max_cards:
                break
            if card.card_id in selected_card_ids:
                continue
            selected_cards.append(card)
            selected_card_ids.add(card.card_id)
        return ReviewHarnessContext(
            version=str(self.manifest.get("version", "1")),
            signals=signals,
            skills=selected,
            knowledge_cards=selected_cards,
            policy_types=policy_types,
            candidate_policy_types=candidate_policy_types,
        )

    @property
    def max_policies_per_batch(self) -> int:
        return max(1, int(self.manifest.get("max_policies_per_batch", 2)))
