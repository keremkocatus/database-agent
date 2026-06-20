"""OpenAI-uyumlu chat adapter — vLLM + OpenAI + Ollama (design/09).

Üçü de `/v1/chat/completions` uyumlu; fark = base_url / model / api_key / caps. Sağlayıcı yanıtı
tek `LLMResponse`'a normalize edilir (normalize_response ayrı → HTTP'siz test edilebilir).
"""

from __future__ import annotations

import json
from typing import Any

from src.application.dtos.llm import Caps, LLMResponse, Msg, ToolCall, ToolSpec, Usage
from src.infrastructure.llm.base import post_json


class OpenAICompatibleChat:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        caps: Caps | None = None,
        is_cloud: bool = False,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._caps = caps or Caps(tool_calling=True, json_mode=True, max_context=8192)
        self._is_cloud = is_cloud
        self._timeout = timeout

    @property
    def caps(self) -> Caps:
        return self._caps

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def is_cloud(self) -> bool:
        return self._is_cloud

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
            model=self._model,
            messages=messages,
            tools=tools,
            schema=schema if self._caps.json_mode else None,
            temperature=temperature,
            seed=seed,
            max_tokens=max_tokens,
        )
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        data = post_json(
            f"{self._base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self._timeout,
        )
        return normalize_response(data)


def build_request(
    *,
    model: str,
    messages: list[Msg],
    tools: list[ToolSpec] | None,
    schema: dict[str, Any] | None,
    temperature: float,
    seed: int | None,
    max_tokens: int,
) -> dict[str, Any]:
    msgs: list[dict[str, Any]] = []
    for m in messages:
        entry: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_call_id:
            entry["tool_call_id"] = m.tool_call_id
        if m.name:
            entry["name"] = m.name
        msgs.append(entry)

    payload: dict[str, Any] = {
        "model": model,
        "messages": msgs,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if seed is not None:
        payload["seed"] = seed
    if tools:
        payload["tools"] = [
            {"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.parameters}}
            for t in tools
        ]
    if schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "structured_output", "schema": schema, "strict": True},
        }
    return payload


def normalize_response(data: dict[str, Any]) -> LLMResponse:
    """OpenAI chat-completions yanıtı → LLMResponse (test edilebilir saf fonksiyon)."""
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message", {}) or {}
    content = message.get("content")

    tool_calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function", {})
        args = fn.get("arguments")
        try:
            parsed_args = json.loads(args) if isinstance(args, str) else (args or {})
        except json.JSONDecodeError:
            parsed_args = {"_raw": args}
        tool_calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=parsed_args))

    parsed: dict[str, Any] | None = None
    if content:
        stripped = content.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None

    usage_raw = data.get("usage") or {}
    return LLMResponse(
        text=content,
        tool_calls=tool_calls,
        parsed=parsed,
        usage=Usage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0),
            completion_tokens=usage_raw.get("completion_tokens", 0),
        ),
        model_id=data.get("model", ""),
        finish_reason=choice.get("finish_reason"),
    )
