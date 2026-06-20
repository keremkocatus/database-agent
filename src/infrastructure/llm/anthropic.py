"""Anthropic (Claude) chat adapter — /v1/messages (design/09).

Anthropic embedding vermez (design/09); yalnızca chat. JSON-schema native yok → json_mode=False;
structured() prompt-tabanlı + doğrula/retry kullanır.
"""

from __future__ import annotations

import json
from typing import Any

from src.application.dtos.llm import Caps, LLMResponse, Msg, ToolCall, ToolSpec, Usage
from src.infrastructure.llm.base import post_json

_API_URL = "https://api.anthropic.com/v1/messages"
_VERSION = "2023-06-01"


class AnthropicChat:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = _API_URL,
        max_context: int = 200_000,
        timeout: float = 60.0,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._url = base_url
        self._caps = Caps(tool_calling=True, json_mode=False, max_context=max_context)
        self._timeout = timeout

    @property
    def caps(self) -> Caps:
        return self._caps

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def is_cloud(self) -> bool:
        return True

    def chat(
        self,
        messages: list[Msg],
        *,
        tools: list[ToolSpec] | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        seed: int | None = None,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        payload = build_request(
            model=self._model, messages=messages, tools=tools,
            temperature=temperature, max_tokens=max_tokens,
        )
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": _VERSION,
            "content-type": "application/json",
        }
        data = post_json(self._url, headers=headers, json=payload, timeout=self._timeout)
        return normalize_response(data)


def build_request(
    *,
    model: str,
    messages: list[Msg],
    tools: list[ToolSpec] | None,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    system_parts = [m.content for m in messages if m.role == "system"]
    convo: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue
        if m.role == "tool":
            convo.append({
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": m.tool_call_id or "", "content": m.content}],
            })
        else:
            convo.append({"role": m.role, "content": m.content})

    payload: dict[str, Any] = {
        "model": model,
        "messages": convo,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)
    if tools:
        payload["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]
    return payload


def normalize_response(data: dict[str, Any]) -> LLMResponse:
    """Anthropic messages yanıtı → LLMResponse (saf fonksiyon)."""
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in data.get("content") or []:
        btype = block.get("type")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append(
                ToolCall(id=block.get("id", ""), name=block.get("name", ""), arguments=block.get("input", {}))
            )
    text = "".join(text_parts) or None

    parsed: dict[str, Any] | None = None
    if text:
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None

    usage_raw = data.get("usage") or {}
    return LLMResponse(
        text=text,
        tool_calls=tool_calls,
        parsed=parsed,
        usage=Usage(
            prompt_tokens=usage_raw.get("input_tokens", 0),
            completion_tokens=usage_raw.get("output_tokens", 0),
        ),
        model_id=data.get("model", ""),
        finish_reason=data.get("stop_reason"),
    )
