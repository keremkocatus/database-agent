"""Enrichment kalite kapısı (design/05) — "zehirli embedding" önleme.

LLM özetinde/açıklamasında anılan tablo/kolon/parametre adları yapısal metadata'da gerçekten var mı?
Anılan ama var-olmayan ad = halüsinasyon sinyali → özet reddedilir (boş + summary_confidence: low).
Determinist ve ucuz; JSON-şema doğrulamasının (design/09) üstüne biner (içeriğin gerçekliği).
"""

from __future__ import annotations

import re

# Özette geçebilecek tanımlayıcı kalıpları:
_PARAM = re.compile(r"@\w+")
_QUALIFIED = re.compile(r"\b\w+\.\w+\b")  # schema.table
_UPPER_TOKEN = re.compile(r"\b[A-ZİŞÇĞÖÜ][A-ZİŞÇĞÖÜ0-9_]{3,}\b")  # ALL_CAPS tablo/SP adı


def validate_summary(summary: str, valid_identifiers: set[str]) -> bool:
    """Özette anılan tanımlayıcılar geçerli kümede mi? Hepsi geçerliyse True (özet kabul)."""
    valid = {v.lower() for v in valid_identifiers}
    for token in _cited_identifiers(summary):
        low = token.lower()
        # schema.table ise hem tam hem son parça kabul edilir
        if low in valid:
            continue
        if "." in low and low.split(".")[-1] in valid:
            continue
        return False  # anılan ama yapısal metadata'da yok → halüsinasyon
    return True


def collect_identifiers(*, tables: list[str], columns: list[str], params: list[str]) -> set[str]:
    """Yapısal metadata'dan geçerli ad kümesi (tablo, schema.table, kolon, @param)."""
    ids: set[str] = set()
    for t in tables:
        ids.add(t)
        if "." in t:
            ids.add(t.split(".")[-1])
    ids.update(columns)
    ids.update(params)
    return ids


def _cited_identifiers(summary: str) -> set[str]:
    cited: set[str] = set()
    cited.update(_PARAM.findall(summary))
    cited.update(_QUALIFIED.findall(summary))
    cited.update(_UPPER_TOKEN.findall(summary))
    return cited
