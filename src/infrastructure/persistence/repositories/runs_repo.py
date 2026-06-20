"""RunsRepo — sync run özeti (design/01 platform katmanı)."""

from __future__ import annotations

import json
from typing import Any

from src.infrastructure.persistence.database_client import DatabaseClient


class RunsRepo:
    def __init__(self, db: DatabaseClient) -> None:
        self._db = db

    async def start_run(self, server: str, database: str) -> str:
        row = await self._db.fetch_one(
            "INSERT INTO runs (server, database, status) VALUES (:s, :d, 'running') "
            "RETURNING id::text AS id",
            {"s": server, "d": database},
        )
        assert row is not None
        return row["id"]

    async def finish_run(
        self, run_id: str, status: str, counts: dict[str, int], errors: list[str]
    ) -> None:
        await self._db.execute(
            "UPDATE runs SET finished_at = now(), status = :st, "
            "counts = CAST(:counts AS jsonb), errors = CAST(:errors AS jsonb) WHERE id = CAST(:id AS uuid)",
            {
                "id": run_id,
                "st": status,
                "counts": json.dumps(counts),
                "errors": json.dumps(errors),
            },
        )

    async def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        return await self._db.fetch_all(
            "SELECT id::text AS id, server, database, started_at, finished_at, status, counts, errors "
            "FROM runs ORDER BY started_at DESC LIMIT :n",
            {"n": limit},
        )
