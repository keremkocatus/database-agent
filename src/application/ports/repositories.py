"""Postgres repository port'ları (design/01, /04). Async (asyncpg engine altında)."""

from __future__ import annotations

from typing import Any, Protocol

from src.domain.entities.catalog import CatalogObject, DependencyEdge, TableDef


class CatalogRepoPort(Protocol):
    async def upsert_object(self, obj: CatalogObject) -> None:
        """Nesne satırını upsert et (kenarsız). Kenarlar replace_edges ile (iki-geçiş, FK)."""
        ...

    async def upsert_table(self, table: TableDef) -> None:
        ...

    async def replace_edges(self, src_uid: str, edges: list[DependencyEdge]) -> None:
        """src_uid'in tüm kenarlarını sil + yenilerini yaz (TEK transaction, design/01).

        Yalnızca hedefi (dst_uid) objects'te var olan kenarlar yazılır — kapsam-dışı hedefler
        düşürülür (design/04 kapsam kuralı).
        """
        ...

    async def known_uids(self, server: str, database: str) -> set[str]:
        """Bir DB'deki tüm uid'ler — kenar hedef-doğrulaması için (kapsam-içi)."""
        ...

    async def get_object(self, uid_or_alias: str) -> dict[str, Any] | None:
        ...

    async def remove_object(self, uid: str) -> None:
        ...

    async def counts(self, server: str | None = None, database: str | None = None) -> dict[str, int]:
        """type → adet (status için)."""
        ...

    async def resolve_uid(self, uid_or_alias: str) -> str | None:
        ...


class GraphRepoPort(Protocol):
    async def get_dependencies(self, uid: str, max_depth: int = 6) -> list[dict[str, Any]]:
        """Çağrılan nesneler + okunan/yazılan tablolar (recursive CTE, cycle guard)."""
        ...

    async def get_dependents(self, uid: str, max_depth: int = 6) -> list[dict[str, Any]]:
        """'Bu nesneyi/tabloyu kim kullanıyor' (etki analizi, read/write ayrımlı)."""
        ...

    async def neighbors(self, uid: str) -> list[dict[str, Any]]:
        """Tek-hop doğrudan kenarlar."""
        ...


class RunsRepoPort(Protocol):
    async def start_run(self, server: str, database: str) -> str:
        ...

    async def finish_run(
        self, run_id: str, status: str, counts: dict[str, int], errors: list[str]
    ) -> None:
        ...

    async def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        ...
