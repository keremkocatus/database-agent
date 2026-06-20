"""Değişim tespiti — modify_date ile aday, hash ile doğrulama (design/03).

Kimlik = MSSQL ``object_id`` (rename'de sabit). Akış:
  - Yeni object_id            → added
  - Kaybolan object_id        → removed (yalnızca keşif TAM başarılıysa → soft-delete güvenliği)
  - Aynı id, farklı ad/şema   → renamed (içerik de değiştiyse ayrıca changed işlenir)
  - modify_date değişti       → aday → hash farklıysa changed, aynıysa unchanged
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.entities.manifest import InventoryItem, Manifest


@dataclass
class DiffResult:
    added: list[InventoryItem] = field(default_factory=list)
    candidates: list[InventoryItem] = field(default_factory=list)  # modify_date değişti → hash gerek
    renamed: list[tuple[InventoryItem, InventoryItem]] = field(default_factory=list)  # (old, new)
    removed: list[InventoryItem] = field(default_factory=list)
    unchanged: list[InventoryItem] = field(default_factory=list)


def diff_inventory(
    previous: Manifest | None,
    current_items: list[InventoryItem],
    *,
    discovery_complete: bool,
) -> DiffResult:
    """Önceki manifest ile yeni envanteri karşılaştır (tanım çekmeden, ucuz ön-filtre).

    ``discovery_complete=False`` ise (DB degraded/kısmi) eksik nesneler SİLİNMEZ — son iyi
    snapshot korunur (design/03 soft-delete güvenliği).
    """
    result = DiffResult()
    prev_by_id = previous.by_object_id() if previous else {}
    seen_ids: set[int] = set()

    for item in current_items:
        seen_ids.add(item.object_id)
        prev = prev_by_id.get(item.object_id)
        if prev is None:
            result.added.append(item)
            continue

        # Aynı object_id, ad/şema değişti → rename/taşıma.
        if prev.name != item.name or prev.schema != item.schema:
            result.renamed.append((prev, item))

        # modify_date ucuz ön-filtre; eşitse içerik değişmemiş kabul (hash'e gerek yok).
        if _modify_changed(prev, item):
            item.hash = prev.hash  # eski hash'i taşı, doğrulamada karşılaştırılır
            result.candidates.append(item)
        elif prev.name == item.name and prev.schema == item.schema:
            item.hash = prev.hash
            result.unchanged.append(item)

    if discovery_complete and previous:
        for obj_id, prev in prev_by_id.items():
            if obj_id not in seen_ids:
                result.removed.append(prev)

    return result


def _modify_changed(prev: InventoryItem, cur: InventoryItem) -> bool:
    if prev.modify_date is None or cur.modify_date is None:
        return True  # bilinmiyorsa aday say (güvenli taraf)
    return prev.modify_date != cur.modify_date
