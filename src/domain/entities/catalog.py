"""Çekirdek katalog varlıkları (design/03, /04, /05).

Bu varlıklar framework-bağımsızdır (saf dataclass). Disk (meta.json/table.json) ve
Postgres (objects/edges) temsillerinin ortak iç modelidir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

ObjectType = Literal["procedure", "view", "function", "trigger", "table"]
EdgeKind = Literal["calls", "reads", "writes"]
PipelineState = Literal["extracted", "parsed", "enriched", "embedded", "indexed", "parse_error"]


@dataclass
class Parameter:
    name: str
    type: str
    udt: bool = False
    output: bool = False
    default: str | None = None


@dataclass
class TableRef:
    name: str  # schema-qualified, ör. "dbo.TEKLIF"
    columns: list[str] = field(default_factory=list)


@dataclass
class CatalogObject:
    """Programlanabilir nesne (SP/View/Function/Trigger). Yapısal metadata = meta.json (design/04)."""

    uid: str
    alias: str
    server: str
    database: str
    schema: str
    name: str
    type: ObjectType
    object_id: int
    modify_date: datetime | None = None
    content_hash: str | None = None
    state: PipelineState = "extracted"
    flags: dict[str, Any] = field(default_factory=dict)

    parameters: list[Parameter] = field(default_factory=list)
    returns: dict[str, Any] | None = None
    reads_tables: list[TableRef] = field(default_factory=list)
    writes_tables: list[TableRef] = field(default_factory=list)
    calls_objects: list[str] = field(default_factory=list)
    temp_tables: list[str] = field(default_factory=list)
    loc: int = 0

    human_description: str | None = None
    summary: str | None = None  # M4 (LLM) doldurur
    category: str | None = None  # M4 (LLM) doldurur

    def meta_dict(self) -> dict[str, Any]:
        """Disk *.meta.json gövdesi (design/04 şeması)."""
        return {
            "schema_version": 1,
            "uid": self.uid,
            "alias": self.alias,
            "server": self.server,
            "database": self.database,
            "schema": self.schema,
            "name": self.name,
            "type": self.type,
            "object_id": self.object_id,
            "modify_date": self.modify_date.isoformat() if self.modify_date else None,
            "hash": self.content_hash,
            "flags": self.flags,
            "parameters": [vars(p) for p in self.parameters],
            "returns": self.returns,
            "reads_tables": [vars(t) for t in self.reads_tables],
            "writes_tables": [vars(t) for t in self.writes_tables],
            "calls_objects": self.calls_objects,
            "temp_tables": self.temp_tables,
            "loc": self.loc,
            "human_description": self.human_description,
            "summary": self.summary,
            "category": self.category,
            "state": self.state,
        }


@dataclass
class Column:
    name: str
    type: str
    nullable: bool = True
    identity: bool = False
    pk: bool = False
    computed: str | None = None
    collation: str | None = None
    fk: dict[str, str] | None = None
    human_description: str | None = None
    description: str | None = None  # M4 (LLM)


@dataclass
class ForeignKey:
    name: str
    from_columns: list[str]
    to_table: str
    to_columns: list[str]


@dataclass
class TableDef:
    """Tablo/View sözlüğü kaydı (design/05) = tables/<schema>/<TABLE>.json."""

    uid: str
    alias: str
    server: str
    database: str
    schema: str
    name: str
    object_id: int
    object_kind: Literal["table", "view"] = "table"
    row_count_estimate: int | None = None
    data_size_mb: float | None = None
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    check_constraints: list[dict[str, str]] = field(default_factory=list)
    indexes: list[dict[str, Any]] = field(default_factory=list)
    read_by_objects: list[str] = field(default_factory=list)
    written_by_objects: list[str] = field(default_factory=list)
    content_hash: str | None = None
    human_description: str | None = None
    table_description: str | None = None  # M4 (LLM)

    def table_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "uid": self.uid,
            "alias": self.alias,
            "server": self.server,
            "database": self.database,
            "schema": self.schema,
            "name": self.name,
            "object_id": self.object_id,
            "object_kind": self.object_kind,
            "row_count_estimate": self.row_count_estimate,
            "data_size_mb": self.data_size_mb,
            "columns": [vars(c) for c in self.columns],
            "primary_key": self.primary_key,
            "foreign_keys": [
                {
                    "name": fk.name,
                    "from": fk.from_columns,
                    "to_table": fk.to_table,
                    "to": fk.to_columns,
                }
                for fk in self.foreign_keys
            ],
            "check_constraints": self.check_constraints,
            "indexes": self.indexes,
            "read_by_objects": self.read_by_objects,
            "written_by_objects": self.written_by_objects,
            "human_description": self.human_description,
            "table_description": self.table_description,
            "hash": self.content_hash,
        }


@dataclass(frozen=True)
class DependencyEdge:
    """Bağımlılık grafiği kenarı (design/04). Yalnızca kapsam-içi hedefler."""

    src_uid: str
    dst_uid: str
    kind: EdgeKind
    via_synonym: bool = False
    is_updated: bool = False
