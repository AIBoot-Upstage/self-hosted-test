from __future__ import annotations

import math
import re
from pathlib import Path

from backend.app.core.schemas import PolicyChunk, ReviewRequest

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./-]+")
POLICY_GLOBS = ("*.md", "**/*.md", "CODEOWNERS", ".github/pull_request_template.md")


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_PATTERN.findall(text) if len(token) > 2}


def _policy_type(path: Path, content: str) -> str:
    lowered = f"{path} {content[:500]}".lower()
    for candidate in ("security", "api", "test", "style", "architecture"):
        if candidate in lowered:
            return candidate
    return "general"


def _split_markdown(source_path: Path, content: str, max_chars: int = 1800) -> list[PolicyChunk]:
    chunks: list[PolicyChunk] = []
    section_title = source_path.name
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        text = "\n".join(buffer).strip()
        if not text:
            buffer.clear()
            return
        while len(text) > max_chars:
            chunks.append(
                PolicyChunk(
                    source_path=str(source_path),
                    section_title=section_title,
                    content=text[:max_chars],
                    policy_type=_policy_type(source_path, text),
                )
            )
            text = text[max_chars:]
        chunks.append(
            PolicyChunk(
                source_path=str(source_path),
                section_title=section_title,
                content=text,
                policy_type=_policy_type(source_path, text),
            )
        )
        buffer.clear()

    for line in content.splitlines():
        if line.startswith("#"):
            flush()
            section_title = line.lstrip("#").strip() or source_path.name
        buffer.append(line)
    flush()
    return chunks


class LocalPolicyIndex:
    """Small local RAG index used for MVP and tests.

    The production-ready path is to replace this with pgvector while keeping the
    same retrieve contract.
    """

    def __init__(self, policy_root: Path) -> None:
        self.policy_root = policy_root

    def _candidate_files(self) -> list[Path]:
        if not self.policy_root.exists():
            return []
        files: set[Path] = set()
        for pattern in POLICY_GLOBS:
            for path in self.policy_root.glob(pattern):
                if path.is_file():
                    files.add(path)
        return sorted(files)

    def load_chunks(self) -> list[PolicyChunk]:
        chunks: list[PolicyChunk] = []
        for path in self._candidate_files():
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                content = path.read_text(encoding="utf-8", errors="ignore")
            relative_path = path.relative_to(self.policy_root)
            chunks.extend(_split_markdown(relative_path, content))
        return chunks

    def has_policy(self) -> bool:
        return bool(self._candidate_files())

    def sync(self) -> dict[str, int]:
        chunks = self.load_chunks()
        return {
            "indexed_documents": len(self._candidate_files()),
            "indexed_chunks": len(chunks),
        }

    def search(self, request: ReviewRequest, top_k: int = 5) -> list[PolicyChunk]:
        chunks = self.load_chunks()
        if not chunks:
            return []

        query_parts = [
            request.pull_request.title,
            " ".join(changed_file.path for changed_file in request.changed_files),
            " ".join(changed_file.patch[:800] for changed_file in request.changed_files[:5]),
        ]
        query_tokens = _tokens("\n".join(query_parts))
        scored: list[PolicyChunk] = []
        for chunk in chunks:
            chunk_tokens = _tokens(f"{chunk.source_path} {chunk.section_title} {chunk.content}")
            overlap = query_tokens & chunk_tokens
            if not overlap:
                continue
            score = len(overlap) / math.sqrt(max(len(chunk_tokens), 1))
            scored.append(
                PolicyChunk(
                    source_path=chunk.source_path,
                    section_title=chunk.section_title,
                    content=chunk.content,
                    policy_type=chunk.policy_type,
                    score=round(score, 4),
                )
            )

        if not scored:
            return chunks[: min(top_k, len(chunks))]
        return sorted(scored, key=lambda item: item.score, reverse=True)[:top_k]

