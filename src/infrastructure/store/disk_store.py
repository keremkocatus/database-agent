"""Disk object store (design/03) — tür+şema ekseninde stabil ağaç, atomik yazım.

data/<server>/<db>/<type-folder>/<schema>/<NAME>.{sql,meta.json} + tables/<schema>/<NAME>.json
+ _manifest.json, _changelog.jsonl. Gerçek ad daima meta/manifest'te; dosya adı sanitize edilir.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from src.domain.entities.catalog import CatalogObject, TableDef
from src.domain.entities.manifest import ChangeEvent, Manifest
from src.domain.value_objects.identity import sanitize_filename

_TYPE_FOLDER = {
    "procedure": "procedures",
    "view": "views",
    "function": "functions",
    "trigger": "triggers",
    "table": "tables",
}


class DiskObjectStore:
    def __init__(self, data_dir: Path) -> None:
        self._root = Path(data_dir)

    # --- paths -----------------------------------------------------------
    def _db_dir(self, server: str, database: str) -> Path:
        return self._root / sanitize_filename(server) / sanitize_filename(database)

    def _obj_dir(self, obj: CatalogObject) -> Path:
        folder = _TYPE_FOLDER.get(obj.type, "objects")
        return self._db_dir(obj.server, obj.database) / folder / sanitize_filename(obj.schema)

    def _sql_path(self, obj: CatalogObject) -> Path:
        return self._obj_dir(obj) / f"{sanitize_filename(obj.name)}.sql"

    def _meta_path(self, obj: CatalogObject) -> Path:
        return self._obj_dir(obj) / f"{sanitize_filename(obj.name)}.meta.json"

    # --- manifest --------------------------------------------------------
    def load_manifest(self, server: str, database: str) -> Manifest | None:
        path = self._db_dir(server, database) / "_manifest.json"
        if not path.exists():
            return None
        return Manifest.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save_manifest(self, manifest: Manifest) -> None:
        path = self._db_dir(manifest.server, manifest.database) / "_manifest.json"
        _atomic_write(path, json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2))

    # --- definitions -----------------------------------------------------
    def write_definition(self, obj: CatalogObject, sql: str, *, keep_prev: bool = True) -> None:
        sql_path = self._sql_path(obj)
        if keep_prev and sql_path.exists():
            prev = sql_path.with_suffix(".prev.sql")
            _atomic_write(prev, sql_path.read_text(encoding="utf-8"))
        _atomic_write(sql_path, sql)

    def read_definition(self, obj: CatalogObject) -> str | None:
        path = self._sql_path(obj)
        return path.read_text(encoding="utf-8") if path.exists() else None

    def write_meta(self, obj: CatalogObject) -> None:
        _atomic_write(
            self._meta_path(obj), json.dumps(obj.meta_dict(), ensure_ascii=False, indent=2)
        )

    def write_table(self, table: TableDef) -> None:
        path = (
            self._db_dir(table.server, table.database)
            / "tables"
            / sanitize_filename(table.schema)
            / f"{sanitize_filename(table.name)}.json"
        )
        _atomic_write(path, json.dumps(table.table_dict(), ensure_ascii=False, indent=2))

    def append_changelog(self, server: str, database: str, event: ChangeEvent) -> None:
        path = self._db_dir(server, database) / "_changelog.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def remove_object(self, obj: CatalogObject) -> None:
        for path in (self._sql_path(obj), self._meta_path(obj)):
            path.unlink(missing_ok=True)


def _atomic_write(path: Path, content: str) -> None:
    """Temp'e yaz → fsync → rename (çökme anında yarım/bozuk dosya olmaz, design/03)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
