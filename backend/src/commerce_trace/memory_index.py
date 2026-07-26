from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from .memory import MemoryRecord, MemoryStatus


class ChromaMemoryIndex:
    """Rebuildable Chroma index; PostgreSQL and knowledge files remain authoritative."""

    business_collection_name = "business_memory_index"
    tool_collection_name = "tool_memory_index"

    def __init__(self, path: Path, embedding_model: str) -> None:
        self.path = path
        self.embedding_model = embedding_model
        self._client: Any = None
        self._embedding: Any = None
        self._state_path = self.path / "_commerce_trace_index_state.json"

    def _update_state(self, key: str, count: int) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        state: dict[str, Any] = {}
        if self._state_path.exists():
            with suppress(Exception):
                state = json.loads(self._state_path.read_text(encoding="utf-8"))
        state.update(
            {
                "embedding_model": self.embedding_model,
                key: {"ready": True, "count": count},
            }
        )
        self._state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    async def status(self) -> str:
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return "missing"
        expected = {"tool_memory_index", "business_memory_index"}
        ready = all(state.get(key, {}).get("ready") is True for key in expected)
        model_matches = state.get("embedding_model") == self.embedding_model
        return "ready" if ready and model_matches else "missing"

    def _dependencies(self) -> tuple[Any, Any]:
        try:
            import chromadb  # type: ignore[import-not-found]
            from chromadb.utils.embedding_functions import (  # type: ignore[import-not-found]
                SentenceTransformerEmbeddingFunction,
            )
        except ImportError as exc:
            raise RuntimeError("Chroma memory requires: uv sync --extra memory") from exc
        return chromadb, SentenceTransformerEmbeddingFunction

    def _get_client(self) -> Any:
        if self._client is None:
            chromadb, embedding_factory = self._dependencies()
            self.path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.path))
            self._embedding = embedding_factory(model_name=self.embedding_model)
        return self._client

    def _replace_collection(self, name: str) -> Any:
        client = self._get_client()
        with suppress(Exception):
            client.delete_collection(name)
        return client.create_collection(
            name=name,
            embedding_function=self._embedding,
            metadata={
                "hnsw:space": "cosine",
                "embedding_model": self.embedding_model,
                "derived_index": True,
            },
        )

    async def rebuild(self, records: list[MemoryRecord]) -> int:
        def operation() -> int:
            collection = self._replace_collection(self.tool_collection_name)
            active = [
                record
                for record in records
                if record.status in {MemoryStatus.TRUSTED, MemoryStatus.CANDIDATE}
            ]
            if active:
                collection.upsert(
                    ids=[record.memory_id for record in active],
                    documents=[
                        f"{record.question}\n{record.analysis_step}\n{record.normalized_sql}"
                        for record in active
                    ],
                    metadatas=[
                        {
                            "status": record.status.value,
                            "schema_fingerprint": record.schema_fingerprint,
                            "source": record.source,
                        }
                        for record in active
                    ],
                )
            self._update_state(self.tool_collection_name, len(active))
            return len(active)

        return await asyncio.to_thread(operation)

    async def rebuild_business(self, documents: list[dict[str, str]]) -> int:
        def operation() -> int:
            collection = self._replace_collection(self.business_collection_name)
            if documents:
                collection.upsert(
                    ids=[item["id"] for item in documents],
                    documents=[item["content"] for item in documents],
                    metadatas=[
                        {"kind": item["kind"], "version": item["version"]} for item in documents
                    ],
                )
            self._update_state(self.business_collection_name, len(documents))
            return len(documents)

        return await asyncio.to_thread(operation)

    async def search(self, query: str, limit: int = 7) -> list[str]:
        def operation() -> list[str]:
            collection = self._get_client().get_collection(
                self.tool_collection_name,
                embedding_function=self._embedding,
            )
            result = collection.query(query_texts=[query], n_results=limit)
            return list(result["ids"][0]) if result.get("ids") else []

        return await asyncio.to_thread(operation)
