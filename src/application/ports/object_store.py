"""Disk store port'u (design/03). Senkron disk IO."""

from __future__ import annotations

from typing import Protocol

from src.domain.entities.catalog import CatalogObject, TableDef
from src.domain.entities.manifest import ChangeEvent, Manifest


class ObjectStorePort(Protocol):
    def load_manifest(self, server: str, database: str) -> Manifest | None:
        ...

    def save_manifest(self, manifest: Manifest) -> None:
        ...

    def write_definition(
        self, obj: CatalogObject, sql: str, *, keep_prev: bool = True
    ) -> None:
        """Ham .sql'i atomik yaz; varsa eskisini .prev.sql'e taşı."""
        ...

    def read_definition(self, obj: CatalogObject) -> str | None:
        ...

    def write_meta(self, obj: CatalogObject) -> None:
        """*.meta.json (design/04)."""
        ...

    def write_table(self, table: TableDef) -> None:
        """tables/<schema>/<TABLE>.json (design/05)."""
        ...

    def append_changelog(self, server: str, database: str, event: ChangeEvent) -> None:
        ...

    def remove_object(self, obj: CatalogObject) -> None:
        ...
