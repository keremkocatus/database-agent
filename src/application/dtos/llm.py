"""LLM/embedding provider katmanı DTO'ları (design/09).

Tüm sistem yalnızca bu normalize şemaları görür; sağlayıcıya özel format adapter'da çevrilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Msg:
    role: Role
    content: str
    tool_call_id: str | None = None  # role='tool' yanıtında
    name: str | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON-schema


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMResponse:
    """Tek normalize yanıt (design/09): text | tool_calls | parsed."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    parsed: dict[str, Any] | None = None  # structured output (JSON)
    usage: Usage = field(default_factory=Usage)
    model_id: str = ""
    finish_reason: str | None = None


@dataclass(frozen=True)
class Caps:
    tool_calling: bool = False
    json_mode: bool = False
    max_context: int = 8192


@dataclass
class EmbedResult:
    dense: list[float]
    sparse: dict[int, float] | None = None  # BGE-M3 öğrenilmiş sparse (index→weight)
