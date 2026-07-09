from __future__ import annotations

import json
from pathlib import Path

from backend.app.core.schemas import ReviewResult


class LocalJsonStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def save_review(self, result: ReviewResult) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        records = self._read_records()
        records.append(result.to_dict())
        self.path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")

    def list_reviews(
        self,
        limit: int | None = None,
        route_name: str | None = None,
        model_tier: str | None = None,
    ) -> list[dict[str, object]]:
        records = list(reversed(self._read_records()))
        if route_name is not None:
            records = [r for r in records if r.get("route", {}).get("name") == route_name]
        if model_tier is not None:
            records = [r for r in records if r.get("route", {}).get("model_tier") == model_tier]
        return records[:limit] if limit is not None else records

    def get_review(self, review_run_id: str) -> dict[str, object] | None:
        for record in self._read_records():
            if record.get("review_run_id") == review_run_id:
                return record
        return None

    def healthcheck(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_records(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        return payload if isinstance(payload, list) else []
