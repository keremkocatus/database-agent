"""CatalogRepo — objects + edges (ham SQL, design/04)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from src.domain.entities.catalog import CatalogObject, DependencyEdge, TableDef
from src.infrastructure.persistence.database_client import DatabaseClient

_UPSERT_OBJECT = """
INSERT INTO objects (uid, alias, server, database, schema, name, type, object_id, object_kind,
                     modify_date, content_hash, state, flags, meta, human_description, updated_at)
VALUES (:uid, :alias, :server, :database, :schema, :name, :type, :object_id, :object_kind,
        :modify_date, :content_hash, :state, CAST(:flags AS jsonb), CAST(:meta AS jsonb),
        :human_description, now())
ON CONFLICT (uid) DO UPDATE SET
    alias = EXCLUDED.alias, schema = EXCLUDED.schema, name = EXCLUDED.name,
    type = EXCLUDED.type, object_kind = EXCLUDED.object_kind,
    modify_date = EXCLUDED.modify_date, content_hash = EXCLUDED.content_hash,
    state = EXCLUDED.state, flags = EXCLUDED.flags, meta = EXCLUDED.meta,
    human_description = EXCLUDED.human_description, updated_at = now();
"""


class CatalogRepo:
    def __init__(self, db: DatabaseClient) -> None:
        self._db = db

    async def upsert_object(self, obj: CatalogObject) -> None:
        await self._db.execute(_UPSERT_OBJECT, self._object_params(obj))

    async def upsert_table(self, table: TableDef) -> None:
        await self._db.execute(_UPSERT_OBJECT, self._table_params(table))

    async def replace_edges(self, src_uid: str, edges: list[DependencyEdge]) -> None:
        async with self._db.transaction() as conn:
            # Hedef-doğrulama: yalnızca objects'te var olan dst_uid'ler (kapsam-içi, design/04).
            valid_targets: set[str] = set()
            if edges:
                rows = await conn.execute(
                    text("SELECT uid FROM objects WHERE uid = ANY(:uids)"),
                    {"uids": list({e.dst_uid for e in edges})},
                )
                valid_targets = {r[0] for r in rows.all()}

            await conn.execute(text("DELETE FROM edges WHERE src_uid = :src"), {"src": src_uid})
            payload = [
                {
                    "src": e.src_uid,
                    "dst": e.dst_uid,
                    "kind": e.kind,
                    "via_synonym": e.via_synonym,
                    "is_updated": e.is_updated,
                }
                for e in edges
                if e.dst_uid in valid_targets and e.dst_uid != e.src_uid
            ]
            if payload:
                await conn.execute(
                    text(
                        "INSERT INTO edges (src_uid, dst_uid, kind, via_synonym, is_updated) "
                        "VALUES (:src, :dst, :kind, :via_synonym, :is_updated) "
                        "ON CONFLICT (src_uid, dst_uid, kind) DO UPDATE SET "
                        "via_synonym = EXCLUDED.via_synonym, is_updated = EXCLUDED.is_updated"
                    ),
                    payload,
                )

    async def known_uids(self, server: str, database: str) -> set[str]:
        rows = await self._db.fetch_all(
            "SELECT uid FROM objects WHERE server = :s AND database = :d",
            {"s": server, "d": database},
        )
        return {r["uid"] for r in rows}

    async def get_object(self, uid_or_alias: str) -> dict[str, Any] | None:
        return await self._db.fetch_one(
            "SELECT * FROM objects WHERE uid = :v OR alias = :v", {"v": uid_or_alias}
        )

    async def resolve_uid(self, uid_or_alias: str) -> str | None:
        row = await self._db.fetch_one(
            "SELECT uid FROM objects WHERE uid = :v OR alias = :v", {"v": uid_or_alias}
        )
        return row["uid"] if row else None

    async def remove_object(self, uid: str) -> None:
        await self._db.execute("DELETE FROM objects WHERE uid = :uid", {"uid": uid})

    async def counts(self, server: str | None = None, database: str | None = None) -> dict[str, int]:
        clauses, params = [], {}
        if server:
            clauses.append("server = :s")
            params["s"] = server
        if database:
            clauses.append("database = :d")
            params["d"] = database
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = await self._db.fetch_all(
            f"SELECT type, count(*) AS n FROM objects{where} GROUP BY type", params
        )
        return {r["type"]: r["n"] for r in rows}

    # --- mapping helpers -------------------------------------------------
    @staticmethod
    def _object_params(obj: CatalogObject) -> dict[str, Any]:
        return {
            "uid": obj.uid,
            "alias": obj.alias,
            "server": obj.server,
            "database": obj.database,
            "schema": obj.schema,
            "name": obj.name,
            "type": obj.type,
            "object_id": obj.object_id,
            "object_kind": None,
            "modify_date": obj.modify_date,
            "content_hash": obj.content_hash,
            "state": obj.state,
            "flags": json.dumps(obj.flags),
            "meta": json.dumps(obj.meta_dict()),
            "human_description": obj.human_description,
        }

    @staticmethod
    def _table_params(table: TableDef) -> dict[str, Any]:
        return {
            "uid": table.uid,
            "alias": table.alias,
            "server": table.server,
            "database": table.database,
            "schema": table.schema,
            "name": table.name,
            "type": "table",
            "object_id": table.object_id,
            "object_kind": table.object_kind,
            "modify_date": None,
            "content_hash": table.content_hash,
            "state": "parsed",
            "flags": json.dumps({}),
            "meta": json.dumps(table.table_dict()),
            "human_description": table.human_description,
        }
