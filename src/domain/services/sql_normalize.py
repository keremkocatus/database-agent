"""SQL normalizasyonu + içerik hash'i (design/03).

normalize(sql) = satır sonlarını LF'e çevir + trailing whitespace kırp.
  - Yorumları KORU (anlamsal arama için değerli).
  - String literal içini DEĞİŞTİRME (semantik bozulmasın).
"""

from __future__ import annotations

import hashlib


def normalize(sql: str) -> str:
    # CRLF/CR → LF, her satırın sonundaki boşlukları kırp, sondaki boş satırları temizle.
    text = sql.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).rstrip("\n")


def content_hash(sql: str) -> str:
    """SHA256(normalize(sql)) → 'sha256:...' (design/03)."""
    digest = hashlib.sha256(normalize(sql).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
