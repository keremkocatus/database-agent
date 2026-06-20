"""Object/table/category card üretimi (design/07).

Kart = anlam-yoğun, token-verimli temsil; embedding bunu kullanır (ham SQL'i değil).
Özet opsiyonel: yok/düşük-güven → "Özet" satırsız **yapısal-only** kart (nesne aramada görünmez kalmaz).
"""

from __future__ import annotations

from src.domain.entities.catalog import CatalogObject, TableDef


def build_object_card(obj: CatalogObject) -> str:
    lines: list[str] = [f"[{obj.type}] {obj.schema}.{obj.name}"]

    summary = obj.human_description or obj.summary
    if summary:  # yapısal-only fallback: özet yoksa bu satır atlanır (design/07)
        lines.append(f"Özet: {summary}")

    if obj.category:
        lines.append(f"Kategori: {obj.category}")

    if obj.parameters:
        params = ", ".join(f"{p.name} {p.type}" for p in obj.parameters)
        lines.append(f"Parametreler: {params}")

    if obj.returns and obj.returns.get("columns"):
        lines.append("Döner: " + ", ".join(obj.returns["columns"]))

    tables = [t.name for t in obj.reads_tables] + [t.name for t in obj.writes_tables]
    if tables:
        lines.append("Kullandığı tablolar: " + ", ".join(dict.fromkeys(tables)))

    if obj.calls_objects:
        lines.append("Çağırdığı nesneler: " + ", ".join(obj.calls_objects))

    return "\n".join(lines)


def build_table_card(table: TableDef) -> str:
    lines: list[str] = [f"[{table.object_kind}] {table.schema}.{table.name}"]

    desc = table.human_description or table.table_description
    if desc:
        lines.append(f"Açıklama: {desc}")

    if table.columns:
        cols = ", ".join(c.name for c in table.columns)
        lines.append(f"Kolonlar: {cols}")

    if table.foreign_keys:
        fks = ", ".join(f"{fk.to_table}" for fk in table.foreign_keys)
        lines.append(f"İlişkili tablolar (FK): {fks}")

    return "\n".join(lines)


def build_category_card(*, taxonomy: str, category: str, label: str, description: str,
                        key_objects: list[str]) -> str:
    lines = [f"[category:{taxonomy}] {category} — {label}"]
    if description:
        lines.append(description)
    if key_objects:
        lines.append("Önemli nesneler: " + ", ".join(key_objects[:10]))
    return "\n".join(lines)
