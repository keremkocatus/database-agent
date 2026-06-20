"""GraphRepo — bağımlılık grafiği sorguları (design/04).

Recursive CTE; path-dizisi ile cycle guard + derinlik limiti (sonsuz döngü/patlama engellenir).
"""

from __future__ import annotations

from typing import Any

from src.infrastructure.persistence.database_client import DatabaseClient

# Outgoing (bağımlılıklar): bu nesne neyi çağırıyor/okuyor/yazıyor.
_DEPENDENCIES = """
WITH RECURSIVE walk AS (
    SELECT e.src_uid, e.dst_uid, e.kind, e.is_updated, e.via_synonym,
           1 AS depth, ARRAY[e.src_uid, e.dst_uid] AS path
    FROM edges e
    WHERE e.src_uid = :uid
    UNION ALL
    SELECT e.src_uid, e.dst_uid, e.kind, e.is_updated, e.via_synonym,
           w.depth + 1, w.path || e.dst_uid
    FROM edges e
    JOIN walk w ON e.src_uid = w.dst_uid
    WHERE w.depth < :max_depth
      AND NOT (e.dst_uid = ANY(w.path))      -- cycle guard
)
SELECT DISTINCT w.dst_uid AS uid, o.alias, o.type, w.kind, w.is_updated, w.via_synonym,
       min(w.depth) AS depth
FROM walk w
LEFT JOIN objects o ON o.uid = w.dst_uid
GROUP BY w.dst_uid, o.alias, o.type, w.kind, w.is_updated, w.via_synonym
ORDER BY depth, uid;
"""

# Incoming (bağımlılar): bu nesneyi/tabloyu kim çağırıyor/okuyor/yazıyor.
_DEPENDENTS = """
WITH RECURSIVE walk AS (
    SELECT e.src_uid, e.dst_uid, e.kind, e.is_updated, e.via_synonym,
           1 AS depth, ARRAY[e.dst_uid, e.src_uid] AS path
    FROM edges e
    WHERE e.dst_uid = :uid
    UNION ALL
    SELECT e.src_uid, e.dst_uid, e.kind, e.is_updated, e.via_synonym,
           w.depth + 1, w.path || e.src_uid
    FROM edges e
    JOIN walk w ON e.dst_uid = w.src_uid
    WHERE w.depth < :max_depth
      AND NOT (e.src_uid = ANY(w.path))
)
SELECT DISTINCT w.src_uid AS uid, o.alias, o.type, w.kind, w.is_updated, w.via_synonym,
       min(w.depth) AS depth
FROM walk w
LEFT JOIN objects o ON o.uid = w.src_uid
GROUP BY w.src_uid, o.alias, o.type, w.kind, w.is_updated, w.via_synonym
ORDER BY depth, uid;
"""

_NEIGHBORS = """
SELECT e.dst_uid AS uid, o.alias, o.type, e.kind, e.is_updated, e.via_synonym, 'out' AS direction
FROM edges e LEFT JOIN objects o ON o.uid = e.dst_uid
WHERE e.src_uid = :uid
UNION ALL
SELECT e.src_uid AS uid, o.alias, o.type, e.kind, e.is_updated, e.via_synonym, 'in' AS direction
FROM edges e LEFT JOIN objects o ON o.uid = e.src_uid
WHERE e.dst_uid = :uid
ORDER BY direction, kind, uid;
"""


class GraphRepo:
    def __init__(self, db: DatabaseClient) -> None:
        self._db = db

    async def get_dependencies(self, uid: str, max_depth: int = 6) -> list[dict[str, Any]]:
        return await self._db.fetch_all(_DEPENDENCIES, {"uid": uid, "max_depth": max_depth})

    async def get_dependents(self, uid: str, max_depth: int = 6) -> list[dict[str, Any]]:
        return await self._db.fetch_all(_DEPENDENTS, {"uid": uid, "max_depth": max_depth})

    async def neighbors(self, uid: str) -> list[dict[str, Any]]:
        return await self._db.fetch_all(_NEIGHBORS, {"uid": uid})
