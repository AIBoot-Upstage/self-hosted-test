from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from backend.app.core.config import Settings
from backend.app.core.schemas import PolicyChunk, ReviewRequest

TOKEN_PATTERN = re.compile(r"[\w./-]+", re.UNICODE)
TOKEN_SPLIT_PATTERN = re.compile(r"[_./-]+")
POLICY_GLOBS = ("*.md", "**/*.md", "CODEOWNERS", ".github/pull_request_template.md")
NON_NORMATIVE_SECTION_TITLES = {"적용 범위", "scope", "overview"}
POLICY_TYPE_PATH_HINTS = (
    ("security", "security"),
    ("api", "api"),
    ("test", "test"),
    ("performance", "performance"),
    ("maintainability", "maintainability"),
    ("observability", "observability"),
    ("reliability", "reliability"),
    ("github", "architecture"),
    ("workflow", "architecture"),
    ("style", "style"),
    ("architecture", "architecture"),
)


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw_token in TOKEN_PATTERN.findall(text):
        normalized = raw_token.lower()
        if len(normalized) > 2:
            tokens.add(normalized)
        tokens.update(
            part
            for part in TOKEN_SPLIT_PATTERN.split(normalized)
            if len(part) > 2
        )
    return tokens


def _policy_type(path: Path, content: str) -> str:
    lowered_path = str(path).lower()
    for hint, policy_type in POLICY_TYPE_PATH_HINTS:
        if hint in lowered_path:
            return policy_type
    lowered_content = content[:500].lower()
    for hint, policy_type in POLICY_TYPE_PATH_HINTS:
        if hint in lowered_content:
            return policy_type
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
        if section_title.strip().lower() in NON_NORMATIVE_SECTION_TITLES:
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
            continue
        buffer.append(line)
    flush()
    return chunks


def _query_tokens(request: ReviewRequest) -> set[str]:
    query_parts = [
        request.pull_request.title,
        " ".join(changed_file.path for changed_file in request.changed_files),
        " ".join(check.summary[:1000] for check in request.checks),
        " ".join(changed_file.patch[:1200] for changed_file in request.changed_files[:10]),
    ]
    return _tokens("\n".join(query_parts))


def _score_chunks(chunks: list[PolicyChunk], request: ReviewRequest, top_k: int) -> list[PolicyChunk]:
    if not chunks:
        return []

    query_tokens = _query_tokens(request)
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
        return []
    return sorted(scored, key=lambda item: item.score, reverse=True)[:top_k]


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
        return _score_chunks(chunks, request, top_k)


class PostgresPolicyIndex(LocalPolicyIndex):
    def __init__(self, policy_root: Path, database_url: str) -> None:
        super().__init__(policy_root)
        self.database_url = database_url
        self._schema_ready = False

    def _connect(self):
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
                    CREATE TABLE IF NOT EXISTS policy_chunks (
                        id BIGSERIAL PRIMARY KEY,
                        source_path TEXT NOT NULL,
                        section_title TEXT NOT NULL,
                        content TEXT NOT NULL,
                        policy_type TEXT NOT NULL,
                        content_hash TEXT NOT NULL UNIQUE,
                        embedding VECTOR(1536),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    )
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_policy_chunks_source_path
                    ON policy_chunks (source_path)
                    """
                )
        self._schema_ready = True

    def sync(self) -> dict[str, int]:
        self.ensure_schema()
        chunks = self.load_chunks()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM policy_chunks")
                for chunk in chunks:
                    content_hash = hashlib.sha256(
                        "\n".join(
                            [
                                chunk.source_path,
                                chunk.section_title,
                                chunk.policy_type,
                                chunk.content,
                            ]
                        ).encode("utf-8")
                    ).hexdigest()
                    cur.execute(
                        """
                        INSERT INTO policy_chunks (
                            source_path,
                            section_title,
                            content,
                            policy_type,
                            content_hash
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (content_hash) DO UPDATE SET
                            source_path = EXCLUDED.source_path,
                            section_title = EXCLUDED.section_title,
                            content = EXCLUDED.content,
                            policy_type = EXCLUDED.policy_type,
                            updated_at = now()
                        """,
                        (
                            chunk.source_path,
                            chunk.section_title,
                            chunk.content,
                            chunk.policy_type,
                            content_hash,
                        ),
                    )
        return {
            "indexed_documents": len(self._candidate_files()),
            "indexed_chunks": len(chunks),
        }

    def _load_indexed_chunks(self) -> list[PolicyChunk]:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT source_path, section_title, content, policy_type
                    FROM policy_chunks
                    ORDER BY id
                    """
                )
                return [
                    PolicyChunk(
                        source_path=row[0],
                        section_title=row[1],
                        content=row[2],
                        policy_type=row[3],
                    )
                    for row in cur.fetchall()
                ]

    def has_policy(self) -> bool:
        self.ensure_schema()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM policy_chunks")
                count = int(cur.fetchone()[0])
        if count == 0 and self._candidate_files():
            return self.sync()["indexed_chunks"] > 0
        return count > 0

    def search(self, request: ReviewRequest, top_k: int = 5) -> list[PolicyChunk]:
        chunks = self._load_indexed_chunks()
        if not chunks and self._candidate_files():
            self.sync()
            chunks = self._load_indexed_chunks()
        return _score_chunks(chunks, request, top_k)


def create_policy_index(settings: Settings) -> LocalPolicyIndex:
    if settings.rag_backend == "postgres":
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is required when RAG_BACKEND=postgres")
        return PostgresPolicyIndex(settings.policy_root, settings.database_url)
    return LocalPolicyIndex(settings.policy_root)
