from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    app_env: str = "local"
    api_token: str | None = None
    llm_mode: str = "mock"
    publish_mode: str = "local"
    storage_backend: str = "local"
    rag_backend: str = "local"
    database_url: str | None = None
    upstage_api_key: str | None = None
    github_token: str | None = None
    github_webhook_secret: str | None = None
    github_app_id: str | None = None
    github_app_private_key: str | None = None
    github_app_private_key_path: Path | None = None
    github_api_base_url: str = "https://api.github.com"
    github_webhook_review_mode: str = "after_checks"
    policy_root: Path = Path("policies")
    local_data_dir: Path = Path(".local-data")
    review_store_path: Path = Path(".local-data/reviews.json")
    comment_output_dir: Path = Path(".local-data/comments")
    upstage_api_base_url: str = "https://api.upstage.ai/v1"
    solar3_model: str = "solar-pro3"
    solar3_low_reasoning_effort: str = "low"
    solar3_medium_reasoning_effort: str = "medium"
    solar3_high_reasoning_effort: str = "high"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    @classmethod
    def from_env(cls) -> "Settings":
        local_data_dir = Path(os.getenv("LOCAL_DATA_DIR", ".local-data"))
        return cls(
            app_env=os.getenv("APP_ENV", "local"),
            api_token=os.getenv("AI_REVIEWER_TOKEN") or None,
            llm_mode=os.getenv("LLM_MODE", "mock").lower(),
            publish_mode=os.getenv("PUBLISH_MODE", "local").lower(),
            database_url=os.getenv("DATABASE_URL") or None,
            storage_backend=os.getenv("STORAGE_BACKEND", "").lower()
            or ("postgres" if os.getenv("DATABASE_URL") else "local"),
            rag_backend=os.getenv("RAG_BACKEND", "").lower()
            or ("postgres" if os.getenv("DATABASE_URL") else "local"),
            upstage_api_key=os.getenv("UPSTAGE_API_KEY") or None,
            github_token=os.getenv("GITHUB_TOKEN") or None,
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET") or None,
            github_app_id=os.getenv("GITHUB_APP_ID") or None,
            github_app_private_key=os.getenv("GITHUB_APP_PRIVATE_KEY") or None,
            github_app_private_key_path=(
                Path(os.environ["GITHUB_APP_PRIVATE_KEY_PATH"])
                if os.getenv("GITHUB_APP_PRIVATE_KEY_PATH")
                else None
            ),
            github_api_base_url=os.getenv("GITHUB_API_BASE_URL", "https://api.github.com"),
            github_webhook_review_mode=os.getenv(
                "GITHUB_WEBHOOK_REVIEW_MODE",
                "after_checks",
            ).lower(),
            policy_root=Path(os.getenv("POLICY_ROOT", "policies")),
            local_data_dir=local_data_dir,
            review_store_path=Path(
                os.getenv("REVIEW_STORE_PATH", str(local_data_dir / "reviews.json"))
            ),
            comment_output_dir=Path(
                os.getenv("COMMENT_OUTPUT_DIR", str(local_data_dir / "comments"))
            ),
            upstage_api_base_url=os.getenv("UPSTAGE_API_BASE_URL", "https://api.upstage.ai/v1"),
            solar3_model=os.getenv("SOLAR3_MODEL", "solar-pro3"),
            solar3_low_reasoning_effort=os.getenv("SOLAR3_LOW_REASONING_EFFORT", "low"),
            solar3_medium_reasoning_effort=(
                os.getenv("SOLAR3_MEDIUM_REASONING_EFFORT")
                or os.getenv("SOLAR3_MIDIUM_REASONING_EFFORT")
                or "medium"
            ),
            solar3_high_reasoning_effort=os.getenv("SOLAR3_HIGH_REASONING_EFFORT", "high"),
            langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY") or None,
            langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY") or None,
            langfuse_host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )

    def model_for_tier(self, model_tier: str) -> str:
        if model_tier.startswith("solar3-"):
            return self.solar3_model
        return model_tier

    def reasoning_effort_for_tier(self, model_tier: str) -> str:
        if model_tier == "solar3-low":
            return self.solar3_low_reasoning_effort
        if model_tier == "solar3-medium":
            return self.solar3_medium_reasoning_effort
        if model_tier == "solar3-high":
            return self.solar3_high_reasoning_effort
        return self.solar3_medium_reasoning_effort
