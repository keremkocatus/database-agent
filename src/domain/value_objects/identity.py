"""Hibrit kimlik (design/03): kalıcı uid + okunur alias.

- uid:   ``server_id/database/object_id`` — MSSQL ``object_id`` bir DB içinde sabittir,
         rename'de DEĞİŞMEZ → otorite kimlik.
- alias: ``server/database/schema/name`` — insan-dostu, rename'de değişir (adresleme).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Dosya-güvenli olmayan karakterler; Türkçe karakterler korunur (design/03).
_UNSAFE = re.compile(r'[\\/:*?"<>|\[\]]')


def make_uid(server: str, database: str, object_id: int) -> str:
    return f"{server}/{database}/{object_id}"


def make_alias(server: str, database: str, schema: str, name: str) -> str:
    return f"{server}/{database}/{schema}/{name}"


def sanitize_filename(name: str) -> str:
    """Dosya adı sanitize — gerçek ad her zaman meta/manifest'te tam durur (design/03)."""
    cleaned = _UNSAFE.sub("_", name).strip().rstrip(".")
    return cleaned or "_"


@dataclass(frozen=True)
class Uid:
    server: str
    database: str
    object_id: int

    def __str__(self) -> str:
        return make_uid(self.server, self.database, self.object_id)

    @classmethod
    def parse(cls, value: str) -> "Uid":
        parts = value.split("/")
        if len(parts) != 3:
            raise ValueError(f"Geçersiz uid: {value!r} (beklenen: server/database/object_id)")
        return cls(parts[0], parts[1], int(parts[2]))
