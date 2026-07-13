from __future__ import annotations

from collections.abc import Iterable

from radon.complexity import cc_rank, cc_visit

from backend.app.core.schemas import ChangedFilePayload, ComplexityMetric, ReviewRequest

PYTHON_CYCLOMATIC_COMPLEXITY_THRESHOLD = 15
MAX_SOURCE_CHARS = 200_000


def _python_functions(source: str) -> dict[str, tuple[int, int]]:
    if not source or len(source) > MAX_SOURCE_CHARS:
        return {}
    try:
        blocks = cc_visit(source)
    except (SyntaxError, TypeError, ValueError):
        return {}
    return {
        str(getattr(block, "fullname", block.name)): (block.complexity, block.lineno)
        for block in blocks
        if block.__class__.__name__ == "Function"
    }


def analyze_python_file(
    changed_file: ChangedFilePayload,
    threshold: int = PYTHON_CYCLOMATIC_COMPLEXITY_THRESHOLD,
) -> list[ComplexityMetric]:
    before_functions = _python_functions(changed_file.base_content)
    after_functions = _python_functions(changed_file.head_content)
    metrics: list[ComplexityMetric] = []
    for symbol, (after, line_start) in sorted(after_functions.items()):
        before = before_functions.get(symbol, (0, 0))[0]
        if after == before:
            continue
        metrics.append(
            ComplexityMetric(
                metric_id=(
                    f"python:cyclomatic_complexity:{changed_file.path}:{symbol}"
                ),
                tool="radon",
                metric="cyclomatic_complexity",
                file_path=changed_file.path,
                symbol=symbol,
                line_start=line_start,
                before=before,
                after=after,
                delta=after - before,
                threshold=threshold,
                exceeded_threshold=after > threshold,
                rank_before=cc_rank(max(1, before)),
                rank_after=cc_rank(max(1, after)),
            )
        )
    return metrics


def analyze_complexity(
    request: ReviewRequest,
    threshold: int = PYTHON_CYCLOMATIC_COMPLEXITY_THRESHOLD,
) -> list[ComplexityMetric]:
    if request.review_mode != "deep_quality_review":
        return []
    metrics: list[ComplexityMetric] = []
    python_files: Iterable[ChangedFilePayload] = (
        changed_file
        for changed_file in request.changed_files
        if changed_file.path.lower().endswith(".py") and changed_file.head_content
    )
    for changed_file in python_files:
        metrics.extend(analyze_python_file(changed_file, threshold=threshold))
    return metrics
