"""Salt-okuma sorgu use-case'leri — CLI show/deps/table/status (design/12)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.application.ports.object_store import ObjectStorePort
from src.application.ports.repositories import CatalogRepoPort, GraphRepoPort, RunsRepoPort
from src.domain.entities.catalog import CatalogObject


@dataclass
class ShowResult:
    object: dict[str, Any]
    sql: str | None = None


class ShowObject:
    def __init__(self, catalog: CatalogRepoPort, store: ObjectStorePort) -> None:
        self._catalog = catalog
        self._store = store

    async def execute(self, uid_or_alias: str, *, with_sql: bool = False) -> ShowResult | None:
        row = await self._catalog.get_object(uid_or_alias)
        if row is None:
            return None
        sql = None
        if with_sql:
            obj = CatalogObject(
                uid=row["uid"],
                alias=row["alias"],
                server=row["server"],
                database=row["database"],
                schema=row["schema"],
                name=row["name"],
                type=row["type"],
                object_id=row["object_id"],
            )
            sql = self._store.read_definition(obj)
        return ShowResult(object=row, sql=sql)


class GetDependencies:
    def __init__(self, catalog: CatalogRepoPort, graph: GraphRepoPort) -> None:
        self._catalog = catalog
        self._graph = graph

    async def execute(self, uid_or_alias: str, *, direction: str = "out", max_depth: int = 6):
        uid = await self._catalog.resolve_uid(uid_or_alias)
        if uid is None:
            return None
        if direction == "in":
            return {"uid": uid, "dependents": await self._graph.get_dependents(uid, max_depth)}
        return {"uid": uid, "dependencies": await self._graph.get_dependencies(uid, max_depth)}


class DescribeTable:
    def __init__(self, catalog: CatalogRepoPort) -> None:
        self._catalog = catalog

    async def execute(self, uid_or_alias: str) -> dict[str, Any] | None:
        row = await self._catalog.get_object(uid_or_alias)
        if row is None or row["type"] != "table":
            return None
        return row


class Status:
    def __init__(self, catalog: CatalogRepoPort, runs: RunsRepoPort) -> None:
        self._catalog = catalog
        self._runs = runs

    async def execute(self) -> dict[str, Any]:
        return {
            "counts": await self._catalog.counts(),
            "recent_runs": await self._runs.recent(limit=10),
        }
