"""Keşif manifesti + değişim olayları (design/02, /03)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

ChangeKind = Literal["added", "changed", "removed", "renamed", "unchanged"]


@dataclass
class InventoryItem:
    """Keşiften gelen envanter girdisi (tanım çekilmeden önce)."""

    schema: str
    name: str
    type: str  # procedure|view|function|trigger|table
    object_id: int
    modify_date: datetime | None = None
    hash: str | None = None  # önceki manifest'ten (varsa)


@dataclass
class SynonymItem:
    schema: str
    name: str
    base: str  # 3-parçalı ad: db.schema.name
    cross_db: bool = False


@dataclass
class Manifest:
    """data/<server>/<db>/_manifest.json (design/02)."""

    server: str
    database: str
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = "active"
    objects: list[InventoryItem] = field(default_factory=list)
    synonyms: list[SynonymItem] = field(default_factory=list)

    def by_object_id(self) -> dict[int, InventoryItem]:
        return {it.object_id: it for it in self.objects}

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for it in self.objects:
            counts[it.type] = counts.get(it.type, 0) + 1
        return {
            "server": self.server,
            "database": self.database,
            "discovered_at": self.discovered_at.isoformat(),
            "status": self.status,
            "object_count": counts,
            "objects": [
                {
                    "schema": it.schema,
                    "name": it.name,
                    "type": it.type,
                    "object_id": it.object_id,
                    "modify_date": it.modify_date.isoformat() if it.modify_date else None,
                    "hash": it.hash,
                }
                for it in self.objects
            ],
            "synonyms": [
                {"schema": s.schema, "name": s.name, "base": s.base, "cross_db": s.cross_db}
                for s in self.synonyms
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        def _dt(v: str | None) -> datetime | None:
            return datetime.fromisoformat(v) if v else None

        return cls(
            server=data["server"],
            database=data["database"],
            discovered_at=_dt(data.get("discovered_at")) or datetime.now(timezone.utc),
            status=data.get("status", "active"),
            objects=[
                InventoryItem(
                    schema=o["schema"],
                    name=o["name"],
                    type=o["type"],
                    object_id=o["object_id"],
                    modify_date=_dt(o.get("modify_date")),
                    hash=o.get("hash"),
                )
                for o in data.get("objects", [])
            ],
            synonyms=[
                SynonymItem(
                    schema=s["schema"], name=s["name"], base=s["base"], cross_db=s.get("cross_db", False)
                )
                for s in data.get("synonyms", [])
            ],
        )


@dataclass
class ChangeEvent:
    """_changelog.jsonl satırı (design/03)."""

    object_id: int
    alias: str
    kind: ChangeKind
    old_hash: str | None = None
    new_hash: str | None = None
    run_id: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "at": self.at.isoformat(),
            "object_id": self.object_id,
            "alias": self.alias,
            "kind": self.kind,
            "old_hash": self.old_hash,
            "new_hash": self.new_hash,
            "run_id": self.run_id,
        }
