from __future__ import annotations

from typing import Any

from backend.app.core.schemas import ReviewResult


class PostgresReviewStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._schema_ready = False

    def _connect(self) -> Any:
        try:
            import psycopg
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError("psycopg is not installed. Run `pip install -e .`.") from exc
        return psycopg.connect(self.database_url)

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS review_runs (
                        review_run_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        idempotency_key TEXT NOT NULL,
                        route_name TEXT NOT NULL,
                        model_tier TEXT NOT NULL,
                        overall_risk TEXT NOT NULL,
                        payload JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_review_runs_idempotency
                    ON review_runs (idempotency_key)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_review_runs_created_at
                    ON review_runs (created_at DESC)
                    """
                )
        self._schema_ready = True

    def healthcheck(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()

    def save_review(self, result: ReviewResult) -> None:
        try:
            from psycopg.types.json import Jsonb
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise RuntimeError("psycopg is not installed. Run `pip install -e .`.") from exc

        self.ensure_schema()
        payload = result.to_dict()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO review_runs (
                        review_run_id,
                        status,
                        idempotency_key,
                        route_name,
                        model_tier,
                        overall_risk,
                        payload
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (review_run_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        idempotency_key = EXCLUDED.idempotency_key,
                        route_name = EXCLUDED.route_name,
                        model_tier = EXCLUDED.model_tier,
                        overall_risk = EXCLUDED.overall_risk,
                        payload = EXCLUDED.payload
                    """,
                    (
                        result.review_run_id,
                        result.status,
                        result.idempotency_key,
                        result.route.name,
                        result.route.model_tier,
                        result.summary.overall_risk,
                        Jsonb(payload),
                    ),
                )

    def list_reviews(
        self,
        limit: int | None = None,
        route_name: str | None = None,
        model_tier: str | None = None,
    ) -> list[dict[str, object]]:
        self.ensure_schema()
        conditions: list[str] = []
        params: list[object] = []
        if route_name is not None:
            conditions.append("route_name = %s")
            params.append(route_name)
        if model_tier is not None:
            conditions.append("model_tier = %s")
            params.append(model_tier)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit_clause = "LIMIT %s" if limit is not None else ""
        if limit is not None:
            params.append(limit)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT payload
                    FROM review_runs
                    {where_clause}
                    ORDER BY created_at DESC
                    {limit_clause}
                    """,
                    params,
                )
                return [row[0] for row in cur.fetchall()]

    def get_review(self, review_run_id: str) -> dict[str, object] | None:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT payload FROM review_runs WHERE review_run_id = %s",
                    (review_run_id,),
                )
                row = cur.fetchone()
        return row[0] if row else None
