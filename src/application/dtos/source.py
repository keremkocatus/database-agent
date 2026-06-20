"""Kaynak DB'den (MSSQL) çekilen ham veriler için DTO'lar (design/03, /04, /05)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# MSSQL type kodu → mantıksal nesne tipi (design/02 envanter sorgusu).
TYPE_MAP: dict[str, str] = {
    "P": "procedure",
    "V": "view",
    "FN": "function",
    "IF": "function",
    "TF": "function",
    "TR": "trigger",
    "U": "table",
}


@dataclass
class RawDefinition:
    """sys.sql_modules.definition + semantik bayraklar (design/03)."""

    object_id: int
    schema: str
    name: str
    type: str  # procedure|view|function|trigger
    modify_date: datetime | None
    definition: str | None  # WITH ENCRYPTION → None
    flags: dict[str, Any] = field(default_factory=dict)
    human_description: str | None = None


@dataclass
class ServerSideDependency:
    """sys.sql_expression_dependencies satırı (design/04)."""

    referencing_id: int
    referenced_database: str | None
    referenced_schema: str | None
    referenced_entity: str | None
    referenced_minor: str | None  # kolon (referans kolon lineage'ı)
    is_updated: bool


@dataclass
class ColumnInfo:
    name: str
    data_type: str
    is_udt: bool
    base_type: str | None
    nullable: bool
    is_identity: bool
    collation: str | None
    default_definition: str | None
    computed_definition: str | None
    human_description: str | None = None


@dataclass
class ForeignKeyInfo:
    name: str
    from_column: str
    to_table: str
    to_column: str


@dataclass
class TableSchema:
    """Tablo/view şema bilgisi (design/05)."""

    object_id: int
    schema: str
    name: str
    object_kind: str  # table|view
    columns: list[ColumnInfo] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKeyInfo] = field(default_factory=list)
    check_constraints: list[dict[str, str]] = field(default_factory=list)
    indexes: list[dict[str, Any]] = field(default_factory=list)
    row_count_estimate: int | None = None
    data_size_mb: float | None = None
    human_description: str | None = None
