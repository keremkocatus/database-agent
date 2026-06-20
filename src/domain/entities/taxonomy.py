"""Taksonomi entity (design/06) — DB-başına, kod + veri ayrı."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class Category:
    key: str
    label: str
    description: str = ""
    subcategories: list[str] = field(default_factory=list)


@dataclass
class Taxonomy:
    database: str
    kind: str  # "code" | "data"
    version: int = 1
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    categories: list[Category] = field(default_factory=list)

    def keys(self) -> list[str]:
        return [c.key for c in self.categories]

    def ensure_diger(self) -> None:
        if "diger" not in self.keys():
            self.categories.append(Category(key="diger", label="Diğer / Sınıflandırılmamış"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "kind": self.kind,
            "version": self.version,
            "generated_at": self.generated_at.isoformat(),
            "categories": [
                {"key": c.key, "label": c.label, "description": c.description,
                 "subcategories": c.subcategories}
                for c in self.categories
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Taxonomy":
        return cls(
            database=data["database"],
            kind=data["kind"],
            version=data.get("version", 1),
            generated_at=datetime.fromisoformat(data["generated_at"])
            if data.get("generated_at") else datetime.now(timezone.utc),
            categories=[
                Category(key=c["key"], label=c.get("label", c["key"]),
                         description=c.get("description", ""), subcategories=c.get("subcategories", []))
                for c in data.get("categories", [])
            ],
        )

    @classmethod
    def from_seed(cls, database: str, kind: str, seed: list[str]) -> "Taxonomy":
        tax = cls(database=database, kind=kind, categories=[
            Category(key=k, label=k.replace("-", " ").title()) for k in seed
        ])
        tax.ensure_diger()
        return tax
