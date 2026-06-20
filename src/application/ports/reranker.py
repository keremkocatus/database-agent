"""Reranker provider port'u (design/08, /09).

NOT: Yalnızca port. bge-reranker adapter'ı + retrieval kullanımı M5'te (cross-encoder, torch).
Port'u M3'te tanımlamak provider katmanını (design/09) tamamlar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Scored:
    index: int
    score: float


class RerankerProvider(Protocol):
    def rerank(self, query: str, docs: list[str], top_k: int) -> list[Scored]:
        ...

    @property
    def model_id(self) -> str:
        ...

    @property
    def is_cloud(self) -> bool:
        ...
