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
    upstage_api_key: str | None = None
    github_token: str | None = None
    policy_root: Path = Path("policies")
    local_data_dir: Path = Path(".local-data")
    review_store_path: Path = Path(".local-data/reviews.json")
    comment_output_dir: Path = Path(".local-data/comments")
    solar3_low_model: str = "solar3-low"
    solar3_medium_model: str = "solar3-medium"
    solar3_high_model: str = "solar3-high"

    @classmethod
    def from_env(cls) -> "Settings":
        local_data_dir = Path(os.getenv("LOCAL_DATA_DIR", ".local-data"))
        return cls(
            app_env=os.getenv("APP_ENV", "local"),
            api_token=os.getenv("AI_REVIEWER_TOKEN") or None,
            llm_mode=os.getenv("LLM_MODE", "mock").lower(),
            publish_mode=os.getenv("PUBLISH_MODE", "local").lower(),
            upstage_api_key=os.getenv("UPSTAGE_API_KEY") or None,
            github_token=os.getenv("GITHUB_TOKEN") or None,
            policy_root=Path(os.getenv("POLICY_ROOT", "policies")),
            local_data_dir=local_data_dir,
            review_store_path=Path(
                os.getenv("REVIEW_STORE_PATH", str(local_data_dir / "reviews.json"))
            ),
            comment_output_dir=Path(
                os.getenv("COMMENT_OUTPUT_DIR", str(local_data_dir / "comments"))
            ),
            solar3_low_model=os.getenv("SOLAR3_LOW_MODEL", "solar3-low"),
            solar3_medium_model=os.getenv("SOLAR3_MEDIUM_MODEL", "solar3-medium"),
            solar3_high_model=os.getenv("SOLAR3_HIGH_MODEL", "solar3-high"),
        )

    def model_for_tier(self, model_tier: str) -> str:
        if model_tier == "solar3-low":
            return self.solar3_low_model
        if model_tier == "solar3-medium":
            return self.solar3_medium_model
        if model_tier == "solar3-high":
            return self.solar3_high_model
        return model_tier

