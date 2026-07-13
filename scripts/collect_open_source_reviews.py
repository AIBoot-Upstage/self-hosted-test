from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.evaluation.open_source_reviews import (
    collect_repository_reviews,
    summarize_records,
    write_jsonl,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect public GitHub PR review metadata for offline evaluation."
    )
    parser.add_argument("repository", help="Public repository in owner/name format")
    parser.add_argument("--max-prs", type=int, default=25)
    parser.add_argument("--state", choices=["open", "closed", "all"], default="closed")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".local-data/evaluation/open-source-reviews.jsonl"),
    )
    args = parser.parse_args()

    records = collect_repository_reviews(
        args.repository,
        max_prs=max(1, args.max_prs),
        state=args.state,
    )
    write_jsonl(args.output, records)
    print(json.dumps({"output": str(args.output), **summarize_records(records)}, indent=2))


if __name__ == "__main__":
    main()
