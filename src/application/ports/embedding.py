"""Embedding provider port'u (design/07, /09)."""

from __future__ import annotations

from typing import Literal, Protocol

from src.application.dtos.llm import EmbedResult

EmbedKind = Literal["passage", "query"]


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str], kind: EmbedKind = "passage") -> list[EmbedResult]:
        ...

    @property
    def dim(self) -> int:
        ...

    @property
    def supports_sparse(self) -> bool:
        """BGE-M3 True; cloud dense-only False → retrieval dense+trigram'a düşer (design/08)."""
        ...

    @property
    def model_id(self) -> str:
        """İndeks damgası (design/07)."""
        ...

    @property
    def is_cloud(self) -> bool:
        ...
