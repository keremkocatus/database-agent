"""BuildCatalog — kategori başına catalog.json + README.md (design/06).

catalog.json deterministik (uid referanslı); README iskelet + opsiyonel LLM prose. Yalnızca etkilenen
kategoriler yeniden derlenir (çağıran karar verir).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from src.application.dtos.llm import Msg
from src.application.ports.llm import LLMProvider
from src.application.ports.object_store import ObjectStorePort
from src.domain.entities.catalog import CatalogObject
from src.domain.entities.taxonomy import Taxonomy


class BuildCatalog:
    def __init__(self, store: ObjectStorePort, llm: LLMProvider | None = None) -> None:
        self._store = store
        self._llm = llm

    def execute(
        self, *, server: str, database: str, taxonomy: Taxonomy, objects: list[CatalogObject]
    ) -> dict[str, dict[str, Any]]:
        primary: dict[str, list[CatalogObject]] = defaultdict(list)
        secondary: dict[str, list[CatalogObject]] = defaultdict(list)
        for obj in objects:
            if obj.category:
                primary[obj.category].append(obj)
            for sec in obj.secondary_categories:
                secondary[sec].append(obj)

        label_by_key = {c.key: c.label for c in taxonomy.categories}
        result: dict[str, dict[str, Any]] = {}
        for key in set(primary) | set(secondary):
            members = primary.get(key, [])
            catalog_json = _catalog_json(taxonomy.kind, key, members, secondary.get(key, []))
            readme = self._readme(label_by_key.get(key, key), catalog_json)
            self._store.write_catalog(server, database, taxonomy.kind, key, catalog_json, readme)
            result[key] = catalog_json
        return result

    def _readme(self, label: str, catalog_json: dict[str, Any]) -> str:
        names = catalog_json["key_objects"]
        common = catalog_json["common_tables"]
        name_lines = [f"- {n}" for n in names] or ["- (yok)"]
        table_lines = [f"- {t}" for t in common] or ["- (yok)"]
        skeleton = [
            f"# {label}",
            "",
            f"Nesne sayısı: {catalog_json['object_count']}",
            "",
            "## Önemli nesneler",
            *name_lines,
            "",
            "## Ortak tablolar",
            *table_lines,
        ]
        intro = self._llm_intro(label, names, common)
        if intro:
            skeleton.insert(2, intro + "\n")
        return "\n".join(skeleton) + "\n"

    def _llm_intro(self, label: str, names: list[str], tables: list[str]) -> str | None:
        if self._llm is None:
            return None
        try:
            resp = self._llm.chat(
                [Msg("system", "Kısa, akıcı Türkçe kategori açıklaması yaz (2-3 cümle)."),
                 Msg("user", f"Kategori: {label}\nÖnemli nesneler: {', '.join(names[:8])}\n"
                             f"Ortak tablolar: {', '.join(tables[:8])}")],
                max_tokens=160,
            )
            return (resp.text or "").strip() or None
        except Exception:
            return None


def _catalog_json(
    kind: str, key: str, members: list[CatalogObject], secondary: list[CatalogObject]
) -> dict[str, Any]:
    table_counter: dict[str, int] = defaultdict(int)
    for obj in members:
        for t in obj.reads_tables + obj.writes_tables:
            table_counter[t.name] += 1
    common = [t for t, _ in sorted(table_counter.items(), key=lambda kv: -kv[1])[:8]]
    key_objects = [o.name for o in members[:8]]

    return {
        "taxonomy": kind,
        "category": key,
        "object_count": len(members),
        "objects": [
            {"uid": o.uid, "alias": o.alias, "type": o.type, "is_primary": True,
             "pinned": o.pinned, "summary": o.summary,
             "uses_tables": [t.name for t in o.reads_tables + o.writes_tables]}
            for o in members
        ],
        "secondary_members": [
            {"uid": o.uid, "alias": o.alias, "primary_category": o.category} for o in secondary
        ],
        "common_tables": common,
        "key_objects": key_objects,
    }
