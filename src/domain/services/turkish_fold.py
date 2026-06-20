"""Türkçe-bilinçli fold (design/07) — trigram ad-araması için deterministik normalize.

Sorun: noktalı/noktasız I (İ/ı ↔ I/i) ve collation'a bağlı yanlış katlama → SP_TEKLİF ≠ SP_TEKLIF.
Karar: uygulama katmanında deterministik katlama; I/İ/ı/i hepsi 'i'ye; ş/ç/ğ/ö/ü KORUNUR (aksan ezilmez).
Kullanıcıya ham ad (objects.name); aramaya normalize ad (objects.search_name).
"""

from __future__ import annotations

# Türkçe-özel açık katlama tablosu (str.lower'ın collation bağımlılığını by-pass eder).
_FOLD = {
    "İ": "i", "I": "i", "ı": "i", "i": "i",
    "Ş": "ş", "Ç": "ç", "Ğ": "ğ", "Ö": "ö", "Ü": "ü",
}


def turkish_fold(text: str) -> str:
    out: list[str] = []
    for ch in text:
        if ch in _FOLD:
            out.append(_FOLD[ch])
        else:
            out.append(ch.lower())
    return "".join(out)
