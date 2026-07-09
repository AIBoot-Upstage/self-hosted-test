from __future__ import annotations

from typing import Protocol

from backend.app.core.config import Settings
from backend.app.core.schemas import ReviewResult
from backend.app.storage.local_store import LocalJsonStore
from backend.app.storage.postgres_store import PostgresReviewStore


class ReviewStore(Protocol):
    def save_review(self, result: ReviewResult) -> None:
        ...

    def get_review(self, review_run_id: str) -> dict[str, object] | None:
        ...

    def list_reviews(
        self,
        limit: int | None = None,
        route_name: str | None = None,
        model_tier: str | None = None,
    ) -> list[dict[str, object]]:
        ...

    def healthcheck(self) -> None:
        ...


def create_review_store(settings: Settings) -> ReviewStore:
    if settings.storage_backend == "postgres":
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is required when STORAGE_BACKEND=postgres")
        return PostgresReviewStore(settings.database_url)
    return LocalJsonStore(settings.review_store_path)
