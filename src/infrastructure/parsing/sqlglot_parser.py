"""sqlglot tabanlı T-SQL parser + regex-lite fallback (design/04).

Bağımlılıkların OTORİTESİ server-side'dır; bu parser parametre/dönen-yapı/temp/çağrı gibi
server-side'ın vermediği yapısal detayı çıkarır. Parse başarısızsa regex-lite + partial_parse.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from src.application.ports.parser import ParseResult
from src.domain.entities.catalog import Parameter, TableRef

# CREATE PROC/FUNCTION başlığındaki parametre bloğu (AS/BEGIN'e kadar).
_PARAM_BLOCK = re.compile(
    r"create\s+(?:or\s+alter\s+)?(?:proc(?:edure)?|function)\s+[\w\[\]\.\"]+\s*(\(?)(.*?)\)?\s*\bas\b",
    re.IGNORECASE | re.DOTALL,
)
_PARAM = re.compile(
    r"(@\w+)\s+([\w\[\]\.]+(?:\s*\(\s*\d+(?:\s*,\s*\d+)?\s*\)|\s*\(\s*max\s*\))?)"
    r"(?:\s*=\s*([^,]+?))?(\s+output\b)?\s*(?:,|$)",
    re.IGNORECASE,
)
_TEMP = re.compile(r"#\w+")
_EXEC = re.compile(r"\bexec(?:ute)?\s+(?!\()\[?(\w+)\]?\.?\[?(\w+)?\]?", re.IGNORECASE)
_DYNAMIC = re.compile(r"\bexec(?:ute)?\s*\(|\bsp_executesql\b", re.IGNORECASE)
_RETURNS_TABLE = re.compile(r"\breturns\b.*?\btable\b", re.IGNORECASE | re.DOTALL)


class SqlglotParser:
    def parse(self, sql: str, object_type: str) -> ParseResult:
        result = ParseResult(loc=sql.count("\n") + 1)
        result.has_dynamic_sql = bool(_DYNAMIC.search(sql))
        result.parameters = _extract_parameters(sql)
        result.temp_tables = sorted(set(_TEMP.findall(sql)))

        try:
            statements = sqlglot.parse(sql, dialect="tsql")
        except Exception:
            statements = []

        if not statements:
            # Tam fallback: parametreler regex'ten geldi; yapı kısmi.
            result.partial_parse = True
            result.calls_objects = _regex_calls(sql)
            result.reads_tables = []
            return result

        try:
            reads, writes, calls = _walk_tables(statements)
            result.reads_tables = [TableRef(name=t) for t in sorted(reads)]
            result.writes_tables = [TableRef(name=t) for t in sorted(writes)]
            result.calls_objects = sorted(set(calls) | set(_regex_calls(sql)))
            result.returns = _extract_returns(sql, statements, object_type)
        except Exception:
            result.partial_parse = True
            result.calls_objects = _regex_calls(sql)
        return result


def _extract_parameters(sql: str) -> list[Parameter]:
    m = _PARAM_BLOCK.search(sql)
    if not m:
        return []
    block = m.group(2)
    params: list[Parameter] = []
    for pm in _PARAM.finditer(block):
        name, type_, default, output = pm.groups()
        params.append(
            Parameter(
                name=name,
                type=type_.strip(),
                udt=False,
                output=bool(output),
                default=default.strip() if default else None,
            )
        )
    return params


def _walk_tables(statements: list) -> tuple[set[str], set[str], set[str]]:
    reads: set[str] = set()
    writes: set[str] = set()
    calls: set[str] = set()
    temp_names = set()

    for stmt in statements:
        if stmt is None:
            continue
        # Geçici tabloları (#temp) gerçek tablo sayma.
        for table in stmt.find_all(exp.Table):
            name = _table_name(table)
            if name.lstrip("#").startswith("#") or "#" in name:
                temp_names.add(name)

        for node in stmt.walk():
            if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Merge)):
                target = node.this
                if isinstance(target, exp.Schema):
                    target = target.this
                if isinstance(target, exp.Table):
                    writes.add(_table_name(target))

        for table in stmt.find_all(exp.Table):
            name = _table_name(table)
            if "#" not in name:
                reads.add(name)

    reads -= writes
    return reads, writes, calls


def _table_name(table: exp.Table) -> str:
    parts = [p.name for p in (table.args.get("db"), table.this) if p]
    name = ".".join(filter(None, parts)) if parts else table.name
    return name


def _extract_returns(sql: str, statements: list, object_type: str) -> dict | None:
    if object_type == "function" and _RETURNS_TABLE.search(sql):
        return {"kind": "table", "columns": []}
    # Son SELECT'in kolonları (mümkünse).
    last_select = None
    for stmt in statements:
        if stmt is None:
            continue
        for sel in stmt.find_all(exp.Select):
            last_select = sel
    if last_select is not None:
        cols = []
        for projection in last_select.expressions:
            alias = projection.alias_or_name
            if alias:
                cols.append(alias)
        if cols:
            return {"kind": "resultset", "columns": cols}
    return None


def _regex_calls(sql: str) -> list[str]:
    calls: set[str] = set()
    for schema, name in _EXEC.findall(sql):
        if name:
            calls.add(f"{schema}.{name}")
        elif schema and not schema.startswith("@"):
            calls.add(schema)
    return sorted(calls)
