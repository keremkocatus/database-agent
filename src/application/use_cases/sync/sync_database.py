"""SyncDatabase — M1+M2 deterministik pipeline orkestratörü (LLM'siz).

discover → değişeni seç → extract(.sql) → parse(meta.json) → tablo sözlüğü → Postgres upsert.
İki-geçişli Postgres yazımı: önce tüm satırlar (objects+tables), sonra kenarlar (FK bütünlüğü).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.application.ports.object_store import ObjectStorePort
from src.application.ports.parser import ParserPort
from src.application.ports.repositories import CatalogRepoPort, GraphRepoPort, RunsRepoPort
from src.application.ports.source_db import SourceDbPort
from src.application.dtos.source import RawDefinition, TableSchema
from src.domain.entities.catalog import (
    CatalogObject,
    Column,
    DependencyEdge,
    ForeignKey,
    TableDef,
    TableRef,
)
from src.domain.entities.manifest import ChangeEvent, InventoryItem, Manifest
from src.domain.services.change_detection import diff_inventory
from src.domain.services.dependency_resolver import build_edges
from src.domain.services.sql_normalize import content_hash
from src.domain.value_objects.identity import make_alias, make_uid
from src.infrastructure.settings.config import ServersConfig


@dataclass
class SyncSummary:
    server: str
    database: str
    counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    status: str = "ok"


class SyncDatabase:
    def __init__(
        self,
        *,
        source: SourceDbPort,
        store: ObjectStorePort,
        parser: ParserPort,
        catalog: CatalogRepoPort,
        graph: GraphRepoPort,
        runs: RunsRepoPort,
        config: ServersConfig,
        enrichment=None,  # EnrichmentPipeline | None (M4); None → yapısal-only (M2)
    ) -> None:
        self._source = source
        self._store = store
        self._parser = parser
        self._catalog = catalog
        self._graph = graph
        self._runs = runs
        self._config = config
        self._enrichment = enrichment

    async def execute(self, server_id: str, database: str) -> SyncSummary:
        summary = SyncSummary(server=server_id, database=database)
        run_id = await self._runs.start_run(server_id, database)
        try:
            inventory = self._discover(server_id, database)
            previous = self._store.load_manifest(server_id, database)
            diff = diff_inventory(previous, list(inventory), discovery_complete=True)

            counts = {
                "added": len(diff.added),
                "changed": 0,
                "removed": len(diff.removed),
                "renamed": len(diff.renamed),
                "unchanged": len(diff.unchanged),
                "parse_error": 0,
            }

            definitions = self._source.fetch_definitions(server_id, database)
            dependencies = self._source.fetch_dependencies(server_id, database)
            synonyms = self._source.list_synonyms(server_id, database)
            table_schemas = self._source.fetch_table_schemas(server_id, database)

            # Hangi kod nesneleri içerik olarak değişti? (added + hash-farklı candidate)
            # changed_ids = .sql yazılacaklar; counts["changed"] yalnızca MEVCUT-değişen (added hariç).
            changed_ids = self._resolve_changed(diff, definitions, run_id, server_id, database)
            added_ids = {a.object_id for a in diff.added}
            counts["changed"] = len(changed_ids - added_ids)

            # uid → "schema.name" (meta reads/writes/calls okunur adları için)
            name_by_uid = {
                make_uid(server_id, database, it.object_id): f"{it.schema}.{it.name}"
                for it in inventory
            }
            all_edges = build_edges(
                server=server_id,
                database=database,
                inventory=list(inventory),
                dependencies=dependencies,
                synonyms=synonyms,
            )
            edges_by_src: dict[str, list[DependencyEdge]] = {}
            for e in all_edges:
                edges_by_src.setdefault(e.src_uid, []).append(e)

            # --- PASS A: kod nesneleri (satır + disk) ---
            code_objects: list[CatalogObject] = []
            for it in inventory:
                if it.type == "table":
                    continue
                raw = definitions.get(it.object_id)
                obj = self._build_object(server_id, database, it, raw, edges_by_src, name_by_uid)
                if obj.state == "parse_error":
                    counts["parse_error"] += 1
                # Disk: .sql yalnızca değişende (keep_prev), meta her zaman.
                if raw and raw.definition is not None and it.object_id in changed_ids:
                    self._store.write_definition(obj, raw.definition, keep_prev=True)
                self._store.write_meta(obj)
                await self._catalog.upsert_object(obj)
                code_objects.append(obj)

            # --- PASS A: tablolar/view'lar (satır + disk) ---
            tables = self._build_tables(server_id, database, table_schemas, all_edges, name_by_uid)
            for table in tables:
                self._store.write_table(table)
                await self._catalog.upsert_table(table)

            # --- PASS B: kenarlar (hedefler artık mevcut → FK güvenli) ---
            for obj in code_objects:
                await self._catalog.replace_edges(obj.uid, edges_by_src.get(obj.uid, []))

            # --- M4: anlamsal katman (enrich→taxonomy→categorize→embed→catalog) ---
            if self._enrichment is not None:
                raw_by_uid = {
                    o.uid: (definitions.get(o.object_id).definition if definitions.get(o.object_id) else None)
                    for o in code_objects
                }
                await self._enrichment.run(
                    server=server_id, database=database,
                    code_objects=code_objects, tables=tables, raw_sql_by_uid=raw_by_uid,
                )
                counts["enriched"] = sum(1 for o in code_objects if o.summary_confidence == "ok")
                counts["indexed"] = sum(1 for o in code_objects if o.state == "indexed")

            # --- Silmeler (soft-delete güvenliği geçti) ---
            for removed in diff.removed:
                uid = make_uid(server_id, database, removed.object_id)
                await self._catalog.remove_object(uid)
                self._store.append_changelog(
                    server_id,
                    database,
                    ChangeEvent(
                        object_id=removed.object_id,
                        alias=make_alias(server_id, database, removed.schema, removed.name),
                        kind="removed",
                        old_hash=removed.hash,
                        run_id=run_id,
                    ),
                )

            # Manifest güncelle (yeni hash'lerle).
            self._save_manifest(server_id, database, inventory, definitions, synonyms)

            summary.counts = counts
            await self._runs.finish_run(run_id, "ok", counts, summary.errors)
            return summary
        except Exception as exc:  # pipeline durmaz, run kaydı düşer
            summary.status = "error"
            summary.errors.append(str(exc))
            await self._runs.finish_run(run_id, "error", summary.counts, summary.errors)
            raise

    # --- discovery + exclusion ------------------------------------------
    def _discover(self, server_id: str, database: str) -> list[InventoryItem]:
        raw_inv = self._source.inventory_objects(server_id, database)
        return [
            it
            for it in raw_inv
            if not self._config.is_excluded(
                server=server_id, database=database, schema=it.schema, name=it.name, type_=it.type
            )
        ]

    def _resolve_changed(
        self,
        diff,
        definitions: dict[int, RawDefinition],
        run_id: str,
        server: str,
        database: str,
    ) -> set[int]:
        """added + hash-farklı candidate → değişti. Changelog olayları yazılır."""
        changed: set[int] = set()
        for it in diff.added:
            changed.add(it.object_id)
            self._log_change(server, database, it, "added", run_id, definitions)
        for it in diff.candidates:
            raw = definitions.get(it.object_id)
            new_hash = content_hash(raw.definition) if raw and raw.definition else it.hash
            if new_hash != it.hash:
                changed.add(it.object_id)
                self._log_change(server, database, it, "changed", run_id, definitions, old=it.hash)
        return changed

    def _log_change(self, server, database, it, kind, run_id, definitions, old=None):
        raw = definitions.get(it.object_id)
        new_hash = content_hash(raw.definition) if raw and raw.definition else None
        self._store.append_changelog(
            server,
            database,
            ChangeEvent(
                object_id=it.object_id,
                alias=make_alias(server, database, it.schema, it.name),
                kind=kind,
                old_hash=old,
                new_hash=new_hash,
                run_id=run_id,
            ),
        )

    # --- builders --------------------------------------------------------
    def _build_object(
        self, server, database, it, raw, edges_by_src, name_by_uid
    ) -> CatalogObject:
        uid = make_uid(server, database, it.object_id)
        obj = CatalogObject(
            uid=uid,
            alias=make_alias(server, database, it.schema, it.name),
            server=server,
            database=database,
            schema=it.schema,
            name=it.name,
            type=it.type,  # type: ignore[arg-type]
            object_id=it.object_id,
            modify_date=it.modify_date,
            state="parsed",
        )
        if raw is None or raw.definition is None:
            obj.flags = (raw.flags if raw else {}) or {"encrypted": raw.definition is None if raw else False}
            obj.human_description = raw.human_description if raw else None
            obj.content_hash = None
            return obj

        obj.content_hash = content_hash(raw.definition)
        obj.human_description = raw.human_description
        parsed = self._parser.parse(raw.definition, it.type)
        obj.parameters = parsed.parameters
        obj.returns = parsed.returns
        obj.temp_tables = parsed.temp_tables
        obj.loc = parsed.loc
        obj.flags = {
            **raw.flags,
            "partial_parse": parsed.partial_parse,
            "has_dynamic_sql": parsed.has_dynamic_sql,
        }
        if parsed.parse_error:
            obj.state = "parse_error"

        # reads/writes/calls OTORİTESİ server-side kenarlar (design/04).
        edges = edges_by_src.get(uid, [])
        obj.reads_tables = [
            TableRef(name=name_by_uid.get(e.dst_uid, e.dst_uid)) for e in edges if e.kind == "reads"
        ]
        obj.writes_tables = [
            TableRef(name=name_by_uid.get(e.dst_uid, e.dst_uid)) for e in edges if e.kind == "writes"
        ]
        obj.calls_objects = [
            name_by_uid.get(e.dst_uid, e.dst_uid) for e in edges if e.kind == "calls"
        ]
        return obj

    def _build_tables(
        self, server, database, table_schemas: dict[int, TableSchema], all_edges, name_by_uid
    ) -> list[TableDef]:
        read_by: dict[str, list[str]] = {}
        written_by: dict[str, list[str]] = {}
        for e in all_edges:
            if e.kind == "reads":
                read_by.setdefault(e.dst_uid, []).append(e.src_uid)
            elif e.kind == "writes":
                written_by.setdefault(e.dst_uid, []).append(e.src_uid)

        tables: list[TableDef] = []
        for oid, ts in table_schemas.items():
            uid = make_uid(server, database, oid)
            table = TableDef(
                uid=uid,
                alias=make_alias(server, database, ts.schema, ts.name),
                server=server,
                database=database,
                schema=ts.schema,
                name=ts.name,
                object_id=oid,
                object_kind=ts.object_kind,  # type: ignore[arg-type]
                row_count_estimate=ts.row_count_estimate,
                data_size_mb=ts.data_size_mb,
                human_description=ts.human_description,
                read_by_objects=sorted(set(read_by.get(uid, []))),
                written_by_objects=sorted(set(written_by.get(uid, []))),
            )
            pk_set = set(ts.primary_key)
            fk_by_col = {fk.from_column: fk for fk in ts.foreign_keys}
            for c in ts.columns:
                table.columns.append(
                    Column(
                        name=c.name,
                        type=_format_type(c),
                        nullable=c.nullable,
                        identity=c.is_identity,
                        pk=c.name in pk_set,
                        computed=c.computed_definition,
                        collation=c.collation,
                        fk=(
                            {"to_table": fk_by_col[c.name].to_table, "to_column": fk_by_col[c.name].to_column}
                            if c.name in fk_by_col
                            else None
                        ),
                        human_description=c.human_description,
                    )
                )
            table.primary_key = ts.primary_key
            table.foreign_keys = _group_fks(ts.foreign_keys)
            table.check_constraints = ts.check_constraints
            table.indexes = ts.indexes
            table.content_hash = _table_hash(table)
            tables.append(table)
        return tables

    def _save_manifest(self, server, database, inventory, definitions, synonyms) -> None:
        for it in inventory:
            raw = definitions.get(it.object_id)
            if raw and raw.definition is not None:
                it.hash = content_hash(raw.definition)
        manifest = Manifest(
            server=server,
            database=database,
            discovered_at=datetime.now(timezone.utc),
            objects=list(inventory),
            synonyms=synonyms,
        )
        self._store.save_manifest(manifest)


def _format_type(c) -> str:
    base = c.base_type or c.data_type
    t = base.upper()
    if t in ("DECIMAL", "NUMERIC"):
        return f"{base}({c_precision(c)})"
    if t in ("VARCHAR", "NVARCHAR", "CHAR", "NCHAR", "VARBINARY"):
        length = c_length(c)
        return f"{base}({length})" if length else base
    return base


def c_precision(c) -> str:
    return f"{getattr(c, 'precision', 0) or 18},{getattr(c, 'scale', 0) or 0}"


def c_length(c) -> str | None:
    # max_length DTO'da yok (sadeleştirildi); None → tipi yalın bırak.
    return None


def _group_fks(fks) -> list[ForeignKey]:
    by_name: dict[str, ForeignKey] = {}
    for fk in fks:
        existing = by_name.get(fk.name)
        if existing:
            existing.from_columns.append(fk.from_column)
            existing.to_columns.append(fk.to_column)
        else:
            by_name[fk.name] = ForeignKey(
                name=fk.name,
                from_columns=[fk.from_column],
                to_table=fk.to_table,
                to_columns=[fk.to_column],
            )
    return list(by_name.values())


def _table_hash(table: TableDef) -> str:
    import hashlib
    import json

    payload = json.dumps(
        {
            "columns": [(c.name, c.type, c.nullable, c.pk) for c in table.columns],
            "pk": table.primary_key,
            "fks": [(fk.name, fk.to_table) for fk in table.foreign_keys],
            "checks": table.check_constraints,
        },
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
