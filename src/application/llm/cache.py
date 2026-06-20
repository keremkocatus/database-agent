"""Offline görev önbelleği (design/09) — (prompt-hash + model_id) → yanıt.

Categorizer/enricher (temp 0) aynı girdiyi önbellekten döner → reindex maliyet/süre keser.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

from src.application.dtos.llm import Msg


class LlmCachePort(Protocol):
    async def get(self, key: str) -> dict[str, Any] | None: ...
    async def put(self, key: str, model_id: str, response: dict[str, Any]) -> None: ...


class NullCache:
    """Önbellek kapalı / test için no-op."""

    async def get(self, key: str) -> dict[str, Any] | None:
        return None

    async def put(self, key: str, model_id: str, response: dict[str, Any]) -> None:
        return None


def cache_key(role: str, model_id: str, messages: list[Msg], extra: str = "") -> str:
    h = hashlib.sha256()
    h.update(role.encode())
    h.update(b"\x00")
    h.update(model_id.encode())
    h.update(b"\x00")
    for m in messages:
        h.update(m.role.encode())
        h.update(b":")
        h.update(m.content.encode())
        h.update(b"\n")
    h.update(extra.encode())
    return h.hexdigest()
