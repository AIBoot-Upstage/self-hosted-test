from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.core.schemas import ReviewRequest
from backend.app.services.orchestrator import create_orchestrator
from backend.app.services.rag import create_policy_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a local AI code review.")
    parser.add_argument("payload", type=Path, help="Path to a review request JSON file.")
    parser.add_argument("--sync-policies", action="store_true", help="Print local policy index stats.")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.sync_policies:
        stats = create_policy_index(settings).sync()
        print(json.dumps(stats, indent=2, ensure_ascii=False))

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    request = ReviewRequest.from_dict(payload)
    result = create_orchestrator(settings).run_review(request)
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
