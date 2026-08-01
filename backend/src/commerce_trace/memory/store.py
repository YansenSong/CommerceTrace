"""Confirmed-query memory: Git-tracked Markdown is the source of truth.

Each confirmed NL->SQL pair is one file under ``knowledge/sql/<slug>.md`` with a
JSON-encoded frontmatter block (we control the shape, so no YAML dependency) and an
optional free-text note below it that is preserved across updates.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class KnowledgeEntry(BaseModel):
    slug: str
    question: str
    sqls: list[str]
    created_at: datetime
    updated_at: datetime
    revision: int = 1
    note: str | None = None


_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def is_valid_slug(slug: str) -> bool:
    """Whether ``slug`` is safe to use as a knowledge filename.

    Slugs are produced by ``slugify`` and must not carry path separators or ``..``
    — otherwise a caller-controlled slug could escape the knowledge directory.
    """

    return _SLUG_RE.fullmatch(slug) is not None


def slugify(question: str) -> str:
    """Build a deterministic, collision-resistant slug from a question.

    Latin fragments become a readable stem; the SHA-256 prefix guarantees the slug
    is stable for the same question and unique across different ones (Chinese-only
    questions fall back to a ``query-<hash>`` stem).
    """

    normalized = unicodedata.normalize("NFKC", question).strip().lower()
    ascii_part = re.sub(
        r"[^a-z0-9]+",
        "-",
        re.sub(r"[^\x00-\x7f]", "", normalized),
    ).strip("-")
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:10]
    stem = ascii_part[:48] or "query"
    return f"{stem}-{digest}"


def _dump_frontmatter(entry: KnowledgeEntry) -> str:
    data = {
        "slug": entry.slug,
        "question": entry.question,
        "sqls": entry.sqls,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
        "revision": entry.revision,
    }
    lines = ["---"]
    lines.extend(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in data.items())
    lines.append("---")
    body = f"\n{entry.note}\n" if entry.note else ""
    return "\n".join(lines) + body


def _parse(text: str) -> KnowledgeEntry:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError("invalid knowledge markdown")
    raw: dict[str, object] = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        raw[key.strip()] = json.loads(value.strip())
    note = match.group(2).strip() or None
    return KnowledgeEntry.model_validate({**raw, "note": note})


class MemoryStore:
    """Filesystem-backed store for confirmed-query knowledge.

    ``save`` upserts by deterministic slug (re-confirming a question updates the
    same file in place and bumps the revision). ``recall`` ranks entries by lexical
    similarity to the current question and returns the top few as few-shot examples.
    """

    def __init__(self, directory: Path, top_n: int = 3, min_score: float = 0.3) -> None:
        self.directory = directory
        self.top_n = top_n
        self.min_score = min_score

    def setup(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, slug: str) -> Path:
        if not is_valid_slug(slug):
            raise ValueError(f"invalid knowledge slug: {slug!r}")
        return self.directory / f"{slug}.md"

    def get(self, slug: str) -> KnowledgeEntry | None:
        path = self.path_for(slug)
        if not path.exists():
            return None
        return _parse(path.read_text(encoding="utf-8"))

    def save(
        self,
        question: str,
        sqls: list[str],
        *,
        note: str | None = None,
    ) -> KnowledgeEntry:
        self.setup()
        now = utc_now()
        slug = slugify(question)
        existing = self.get(slug)
        if existing is not None:
            entry = existing.model_copy(
                update={
                    "question": question,
                    "sqls": sqls,
                    "updated_at": now,
                    "revision": existing.revision + 1,
                    "note": note if note is not None else existing.note,
                }
            )
        else:
            entry = KnowledgeEntry(
                slug=slug,
                question=question,
                sqls=sqls,
                created_at=now,
                updated_at=now,
                note=note,
            )
        tmp = self.path_for(slug).with_suffix(".md.tmp")
        tmp.write_text(_dump_frontmatter(entry), encoding="utf-8")
        tmp.replace(self.path_for(slug))
        return entry

    def list_entries(self) -> list[KnowledgeEntry]:
        if not self.directory.exists():
            return []
        entries: list[KnowledgeEntry] = []
        for path in sorted(self.directory.glob("*.md")):
            try:
                entries.append(_parse(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return entries

    def recall(self, question: str, top_n: int | None = None) -> list[KnowledgeEntry]:
        entries = self.list_entries()
        if not entries:
            return []
        scored = [(score_similarity(question, entry.question), entry) for entry in entries]
        scored.sort(key=lambda pair: (-pair[0], pair[1].slug))
        limit = top_n or self.top_n
        return [entry for score, entry in scored if score >= self.min_score][:limit]

    def delete(self, slug: str) -> bool:
        path = self.path_for(slug)
        if path.exists():
            path.unlink()
            return True
        return False


def _tokens(text: str) -> set[str]:
    """Tokenize for lexical similarity: latin words plus CJK bigrams."""

    lowered = text.lower()
    tokens: set[str] = set()
    tokens.update(re.findall(r"[a-z0-9_]+", lowered))
    cjk = re.findall(r"[一-鿿]", lowered)
    tokens.update(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return tokens


def score_similarity(query: str, candidate: str) -> float:
    """Cosine-like token overlap, with a bonus when one question contains the other."""

    query_tokens = _tokens(query)
    candidate_tokens = _tokens(candidate)
    if not query_tokens or not candidate_tokens:
        return 0.0
    overlap = len(query_tokens & candidate_tokens)
    cosine: float = overlap / ((len(query_tokens) * len(candidate_tokens)) ** 0.5)
    if query in candidate or candidate in query:
        cosine += 0.4
    return min(1.0, cosine)
