"""LlmCacheRepo — offline görev önbelleği (design/09). LlmCachePort implementasyonu."""

from __future__ import annotations

import json
from typing import Any

from src.infrastructure.persistence.database_client import DatabaseClient


class LlmCacheRepo:
    def __init__(self, db: DatabaseClient) -> None:
        self._db = db

    async def get(self, key: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT response FROM llm_cache WHERE key=:k", {"k": key})
        if row is None:
            return None
        resp = row["response"]
        return resp if isinstance(resp, dict) else json.loads(resp)

    async def put(self, key: str, model_id: str, response: dict[str, Any]) -> None:
        await self._db.execute(
            "INSERT INTO llm_cache (key, model_id, response) "
            "VALUES (:k, :m, CAST(:r AS jsonb)) ON CONFLICT (key) DO NOTHING",
            {"k": key, "m": model_id, "r": json.dumps(response)},
        )
