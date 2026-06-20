"""MSSQL adapter — SourceDbPort implementasyonu (design/02, /03, /04, /05).

pyodbc + ODBC Driver 18, ApplicationIntent=ReadOnly, APP etiketi, retry/backoff.
pyodbc lazy import — driver kurulu değilse parser/store testleri yine çalışır.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from src.application.dtos.source import (
    TYPE_MAP,
    ColumnInfo,
    ForeignKeyInfo,
    RawDefinition,
    ServerSideDependency,
    TableSchema,
)
from src.domain.entities.manifest import InventoryItem, SynonymItem
from src.infrastructure.settings.config import Defaults, ServerConfig
from src.infrastructure.source.mssql import queries as q


class MssqlAdapter:
    def __init__(self, server: ServerConfig, defaults: Defaults) -> None:
        self._server = server
        self._defaults = defaults

    # --- connection ------------------------------------------------------
    def _conn_str(self, database: str = "master") -> str:
        d = self._defaults
        return (
            f"DRIVER={{{d.driver}}};"
            f"SERVER={self._server.host};DATABASE={database};"
            f"UID={self._server.username()};PWD={self._server.password()};"
            f"Encrypt={'yes' if d.encrypt else 'no'};"
            f"TrustServerCertificate={'yes' if d.trust_server_certificate else 'no'};"
            f"ApplicationIntent={d.application_intent};"
            f"APP={d.app_name};"
        )

    def _connect(self, database: str = "master"):
        import pyodbc  # lazy

        retries = self._defaults.resilience.max_retries
        backoff = self._defaults.resilience.backoff_seconds
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return pyodbc.connect(self._conn_str(database), timeout=15, readonly=True)
            except Exception as exc:  # pyodbc.Error
                last = exc
                if attempt < retries:
                    time.sleep(backoff * (2**attempt))
        raise RuntimeError(f"Bağlantı başarısız ({self._server.id}/{database}): {last}")

    def _query(self, database: str, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        conn = self._connect(database)
        try:
            cur = conn.cursor()
            cur.execute(sql, params) if params else cur.execute(sql)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    # --- discovery -------------------------------------------------------
    def discover_databases(self, server_id: str) -> list[str]:
        rows = self._query("master", q.DISCOVER_DATABASES)
        exclude = set(self._server.exclude_databases)
        return [r["name"] for r in rows if r["name"] not in exclude]

    def inventory_objects(self, server_id: str, database: str) -> list[InventoryItem]:
        rows = self._query(database, q.INVENTORY_OBJECTS)
        items: list[InventoryItem] = []
        for r in rows:
            mapped = TYPE_MAP.get((r["type_code"] or "").strip())
            if not mapped:
                continue
            items.append(
                InventoryItem(
                    schema=r["schema_name"],
                    name=r["object_name"],
                    type=mapped,
                    object_id=int(r["object_id"]),
                    modify_date=r["modify_date"],
                )
            )
        return items

    def list_synonyms(self, server_id: str, database: str) -> list[SynonymItem]:
        rows = self._query(database, q.LIST_SYNONYMS)
        out: list[SynonymItem] = []
        for r in rows:
            base = r["base_object_name"] or ""
            cross_db = base.count(".") >= 2  # db.schema.name → cross-DB adayı
            out.append(
                SynonymItem(schema=r["schema_name"], name=r["synonym_name"], base=base, cross_db=cross_db)
            )
        return out

    # --- extraction ------------------------------------------------------
    def fetch_definitions(self, server_id: str, database: str) -> dict[int, RawDefinition]:
        rows = self._query(database, q.FETCH_DEFINITIONS)
        props = self._extended_props(database)
        out: dict[int, RawDefinition] = {}
        for r in rows:
            oid = int(r["object_id"])
            definition = r["definition"]
            out[oid] = RawDefinition(
                object_id=oid,
                schema=r["schema_name"],
                name=r["object_name"],
                type=TYPE_MAP.get((r["type_code"] or "").strip(), "procedure"),
                modify_date=r["modify_date"],
                definition=definition,
                flags={
                    "encrypted": definition is None,
                    "clr": False,
                    "uses_ansi_nulls": bool(r.get("uses_ansi_nulls")),
                    "uses_quoted_identifier": bool(r.get("uses_quoted_identifier")),
                    "is_recompiled": bool(r.get("is_recompiled")),
                },
                human_description=props.get((oid, 0)),
            )
        return out

    def _extended_props(self, database: str) -> dict[tuple[int, int], str]:
        rows = self._query(database, q.FETCH_EXTENDED_PROPERTIES)
        return {(int(r["major_id"]), int(r["minor_id"])): r["prop_value"] for r in rows}

    def fetch_dependencies(self, server_id: str, database: str) -> list[ServerSideDependency]:
        rows = self._query(database, q.FETCH_DEPENDENCIES)
        return [
            ServerSideDependency(
                referencing_id=int(r["referencing_id"]),
                referenced_database=r["referenced_database_name"],
                referenced_schema=r["referenced_schema_name"],
                referenced_entity=r["referenced_entity_name"],
                referenced_minor=r["referenced_minor_name"],
                is_updated=bool(r["is_updated"]),
            )
            for r in rows
            if r["referenced_entity_name"]
        ]

    # --- table dictionary ------------------------------------------------
    def fetch_table_schemas(self, server_id: str, database: str) -> dict[int, TableSchema]:
        inv = {it.object_id: it for it in self.inventory_objects(server_id, database) if it.type in ("table", "view")}
        if not inv:
            return {}
        props = self._extended_props(database)
        cols = self._group(self._query(database, q.FETCH_COLUMNS), "object_id")
        pks = self._group(self._query(database, q.FETCH_PRIMARY_KEYS), "object_id")
        fks = self._group(self._query(database, q.FETCH_FOREIGN_KEYS), "object_id")
        checks = self._group(self._query(database, q.FETCH_CHECK_CONSTRAINTS), "object_id")
        idxs = self._group(self._query(database, q.FETCH_INDEXES), "object_id")
        stats = {int(r["object_id"]): r for r in self._query(database, q.FETCH_TABLE_STATS)}

        result: dict[int, TableSchema] = {}
        for oid, it in inv.items():
            ts = TableSchema(
                object_id=oid,
                schema=it.schema,
                name=it.name,
                object_kind=it.type,
                row_count_estimate=int(stats[oid]["row_count"]) if oid in stats else None,
                data_size_mb=float(stats[oid]["data_size_mb"]) if oid in stats else None,
                human_description=props.get((oid, 0)),
            )
            ts.columns = [
                ColumnInfo(
                    name=c["column_name"],
                    data_type=c["data_type"],
                    is_udt=bool(c["is_udt"]),
                    base_type=c["base_type"],
                    nullable=bool(c["is_nullable"]),
                    is_identity=bool(c["is_identity"]),
                    collation=c["collation_name"],
                    default_definition=c["default_definition"],
                    computed_definition=c["computed_definition"],
                    human_description=props.get((oid, int(c["column_id"]))),
                )
                for c in cols.get(oid, [])
            ]
            ts.primary_key = [r["column_name"] for r in pks.get(oid, [])]
            ts.foreign_keys = [
                ForeignKeyInfo(
                    name=r["fk_name"],
                    from_column=r["from_column"],
                    to_table=r["to_table"],
                    to_column=r["to_column"],
                )
                for r in fks.get(oid, [])
            ]
            ts.check_constraints = [
                {"name": r["name"], "definition": r["definition"]} for r in checks.get(oid, [])
            ]
            ts.indexes = self._build_indexes(idxs.get(oid, []))
            result[oid] = ts
        return result

    @staticmethod
    def _group(rows: list[dict[str, Any]], key: str) -> dict[int, list[dict[str, Any]]]:
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            grouped[int(r[key])].append(r)
        return grouped

    @staticmethod
    def _build_indexes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_name: dict[str, dict[str, Any]] = {}
        for r in rows:
            entry = by_name.setdefault(
                r["index_name"], {"name": r["index_name"], "columns": [], "unique": bool(r["is_unique"])}
            )
            entry["columns"].append(r["column_name"])
        return list(by_name.values())

    def probe(self, server_id: str) -> tuple[bool, str]:
        try:
            rows = self._query("master", "SELECT @@VERSION AS v")
            version = (rows[0]["v"] if rows else "").splitlines()[0]
            return True, version.strip()
        except Exception as exc:
            return False, str(exc)
