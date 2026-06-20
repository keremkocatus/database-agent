"""SQL parser port'u (design/04). sqlglot adapter arkasında."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from src.domain.entities.catalog import Parameter, TableRef


@dataclass
class ParseResult:
    parameters: list[Parameter] = field(default_factory=list)
    returns: dict | None = None
    reads_tables: list[TableRef] = field(default_factory=list)
    writes_tables: list[TableRef] = field(default_factory=list)
    calls_objects: list[str] = field(default_factory=list)
    temp_tables: list[str] = field(default_factory=list)
    loc: int = 0
    partial_parse: bool = False
    parse_error: bool = False
    has_dynamic_sql: bool = False


class ParserPort(Protocol):
    def parse(self, sql: str, object_type: str) -> ParseResult:
        """Ham T-SQL → yapısal metadata. Başarısızsa regex-lite fallback + partial_parse."""
        ...
