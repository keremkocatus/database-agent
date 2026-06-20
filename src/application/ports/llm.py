"""LLM provider port'u (design/09). Provider-agnostik ince soyutlama."""

from __future__ import annotations

from typing import Any, Protocol

from src.application.dtos.llm import Caps, LLMResponse, Msg, ToolSpec


class LLMProvider(Protocol):
    def chat(
        self,
        messages: list[Msg],
        *,
        tools: list[ToolSpec] | None = None,
        schema: dict[str, Any] | None = None,  # structured output (JSON-schema)
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        ...

    @property
    def caps(self) -> Caps:
        ...

    @property
    def model_id(self) -> str:
        ...

    @property
    def is_cloud(self) -> bool:
        """allow_cloud guard'ı için (design/09/14)."""
        ...
