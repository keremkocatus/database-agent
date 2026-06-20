"""Server-side bağımlılıkları → graph kenarları (design/04).

Kapsam kuralı: yalnızca kapsam-içi (aynı DB'de keşfedilmiş) hedeflere kenar. Cross-DB/linked-server
hedefleri düşürülür (external düğüm yaratılmaz). Kenar tipi hedef tipinden belirlenir:
  - hedef tablo/view  → reads | writes (is_updated)
  - hedef SP/function → calls
"""

from __future__ import annotations

from src.application.dtos.source import ServerSideDependency
from src.domain.entities.catalog import DependencyEdge
from src.domain.entities.manifest import InventoryItem, SynonymItem
from src.domain.value_objects.identity import make_uid

_CODE_TYPES = {"procedure", "function", "view", "trigger"}


def build_edges(
    *,
    server: str,
    database: str,
    inventory: list[InventoryItem],
    dependencies: list[ServerSideDependency],
    synonyms: list[SynonymItem],
) -> list[DependencyEdge]:
    by_object_id = {it.object_id: it for it in inventory}
    by_name: dict[tuple[str, str], InventoryItem] = {
        (it.schema.lower(), it.name.lower()): it for it in inventory
    }
    synonym_targets = {
        (s.schema.lower(), s.name.lower()): s.base.split(".")[-1].strip("[]") for s in synonyms
    }

    edges: list[DependencyEdge] = []
    for dep in dependencies:
        src = by_object_id.get(dep.referencing_id)
        if src is None or not dep.referenced_entity:
            continue
        # Cross-DB hedef → düşür (referans DB başka bir DB ise kapsam dışı kabul).
        if dep.referenced_database and dep.referenced_database != database:
            continue

        schema = (dep.referenced_schema or "dbo").lower()
        entity = dep.referenced_entity.lower()
        via_synonym = False

        target = by_name.get((schema, entity))
        if target is None and (schema, entity) in synonym_targets:
            via_synonym = True
            base_name = synonym_targets[(schema, entity)].lower()
            target = by_name.get((schema, base_name)) or _find_by_name(by_name, base_name)
        if target is None:
            continue  # kapsam dışı / çözülemeyen → düşür

        src_uid = make_uid(server, database, src.object_id)
        dst_uid = make_uid(server, database, target.object_id)
        if src_uid == dst_uid:
            continue

        if target.type in ("table", "view"):
            kind = "writes" if dep.is_updated else "reads"
        else:
            kind = "calls"
        edges.append(
            DependencyEdge(
                src_uid=src_uid,
                dst_uid=dst_uid,
                kind=kind,
                via_synonym=via_synonym,
                is_updated=dep.is_updated,
            )
        )

    # Aynı (src,dst,kind) tekrarlarını sadeleştir.
    seen: set[tuple[str, str, str]] = set()
    unique: list[DependencyEdge] = []
    for e in edges:
        key = (e.src_uid, e.dst_uid, e.kind)
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def _find_by_name(by_name: dict[tuple[str, str], InventoryItem], name: str) -> InventoryItem | None:
    for (_schema, n), item in by_name.items():
        if n == name:
            return item
    return None
