from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path

import aiosqlite

from ..analysis.models import AnalysisEvent, AnalysisRun
from ..analysis.state_machine import AnalysisRunMachine


class AnalysisRunStore:
    """Durable snapshots and append-only events for analysis runs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._write_lock = asyncio.Lock()

    async def setup(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as connection:
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS analysis_runs (
                    run_id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS analysis_runs_owner_idx
                    ON analysis_runs (user_id, conversation_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS analysis_events (
                    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence)
                );
                """
            )
            await connection.commit()

    async def save(self, machine: AnalysisRunMachine) -> None:
        run = machine.run
        events = list(machine.events)
        async with self._write_lock, aiosqlite.connect(self.path) as connection:
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute(
                """
                INSERT INTO analysis_runs
                    (run_id, conversation_id, user_id, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (
                    run.run_id,
                    run.conversation_id,
                    run.user_id,
                    run.model_dump_json(),
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                ),
            )
            for event in events:
                await connection.execute(
                    """
                    INSERT OR IGNORE INTO analysis_events
                        (run_id, sequence, event_type, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event.run_id,
                        event.sequence,
                        event.event_type,
                        json.dumps(event.data, ensure_ascii=False, default=str),
                        event.created_at.isoformat(),
                    ),
                )
            await connection.commit()
        del machine.events[: len(events)]

    async def get(self, user_id: str, run_id: str) -> AnalysisRun | None:
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                """
                SELECT state_json FROM analysis_runs
                WHERE run_id = ? AND user_id = ?
                """,
                (run_id, user_id),
            )
            row = await cursor.fetchone()
        return AnalysisRun.model_validate_json(row[0]) if row is not None else None

    async def latest_for_conversation(
        self,
        user_id: str,
        conversation_id: str,
    ) -> AnalysisRun | None:
        async with aiosqlite.connect(self.path) as connection:
            cursor = await connection.execute(
                """
                SELECT state_json FROM analysis_runs
                WHERE conversation_id = ? AND user_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (conversation_id, user_id),
            )
            row = await cursor.fetchone()
        return AnalysisRun.model_validate_json(row[0]) if row is not None else None

    async def events_after(
        self,
        user_id: str,
        run_id: str,
        *,
        after: int = 0,
    ) -> list[AnalysisEvent] | None:
        if await self.get(user_id, run_id) is None:
            return None
        async with aiosqlite.connect(self.path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """
                SELECT sequence, event_type, payload_json, created_at
                FROM analysis_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence
                """,
                (run_id, after),
            )
            rows = await cursor.fetchall()
        return [
            AnalysisEvent(
                run_id=run_id,
                sequence=int(row["sequence"]),
                event_type=str(row["event_type"]),
                data=json.loads(str(row["payload_json"])),
                created_at=datetime.fromisoformat(str(row["created_at"])),
            )
            for row in rows
        ]

    async def delete_for_conversation(self, user_id: str, conversation_id: str) -> None:
        async with self._write_lock, aiosqlite.connect(self.path) as connection:
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute(
                "DELETE FROM analysis_runs WHERE conversation_id = ? AND user_id = ?",
                (conversation_id, user_id),
            )
            await connection.commit()
