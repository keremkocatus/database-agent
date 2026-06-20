"""EmbeddingsRepo — kart vektörlerini yaz/oku (design/07).

dense vector(1024) string-cast ile; sparse JSONB (M5'te sparsevec'e materyalize). Incremental:
upsert = (uid, kind, model) için eski satırları sil + yeni yaz.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from src.application.dtos.llm import EmbedResult
from src.infrastructure.persistence.database_client import DatabaseClient


class EmbeddingsRepo:
    def __init__(self, db: DatabaseClient) -> None:
        self._db = db

    async def replace(
        self, uid: str, kind: str, content: str, result: EmbedResult, *, model_id: str, dim: int
    ) -> None:
        await self.replace_many(uid, kind, [(content, result)], model_id=model_id, dim=dim)

    async def replace_many(
        self, uid: str, kind: str, items: list[tuple[str, EmbedResult]], *, model_id: str, dim: int
    ) -> None:
        """(uid, kind, model) için eski satırları sil + tüm yeni kartları/chunk'ları yaz (tek transaction)."""
        async with self._db.transaction() as conn:
            await conn.execute(
                text("DELETE FROM embeddings WHERE uid=:uid AND kind=:kind AND embedding_model=:m"),
                {"uid": uid, "kind": kind, "m": model_id},
            )
            for content, result in items:
                await conn.execute(
                    text(
                        "INSERT INTO embeddings (uid, kind, content, embedding, sparse_json, "
                        "embedding_model, dim) VALUES (:uid, :kind, :content, CAST(:embedding AS vector), "
                        "CAST(:sparse AS jsonb), :m, :dim)"
                    ),
                    {
                        "uid": uid,
                        "kind": kind,
                        "content": content,
                        "embedding": _vec_literal(result.dense),
                        "sparse": json.dumps(result.sparse) if result.sparse else None,
                        "m": model_id,
                        "dim": dim,
                    },
                )

    async def delete_for(self, uid: str) -> None:
        await self._db.execute("DELETE FROM embeddings WHERE uid=:uid", {"uid": uid})

    async def stats(self, server: str | None = None, database: str | None = None) -> dict[str, Any]:
        clauses, params = [], {}
        if server:
            clauses.append("o.server=:s")
            params["s"] = server
        if database:
            clauses.append("o.database=:d")
            params["d"] = database
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = await self._db.fetch_all(
            f"SELECT e.kind, count(*) AS n FROM embeddings e "
            f"JOIN objects o ON o.uid=e.uid{where} GROUP BY e.kind",
            params,
        )
        return {r["kind"]: r["n"] for r in rows}


def _vec_literal(dense: list[float]) -> str:
    """pgvector text literal: '[0.1,0.2,...]'."""
    return "[" + ",".join(repr(float(x)) for x in dense) + "]"
