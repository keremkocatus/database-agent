"""Vertex AI (Gemini) chat adapter — generateContent (design/09).

Auth: google-auth (opsiyonel extra [vertex]) → erişim token'ı. Lazy import; kurulu değilse
yapıcıda anlamlı hata. JSON-schema native (responseSchema) destekli → json_mode=True.
"""

from __future__ import annotations

import json
from typing import Any

from src.application.dtos.llm import Caps, LLMResponse, Msg, ToolCall, ToolSpec, Usage
from src.infrastructure.llm.base import post_json


class VertexChat:
    def __init__(
        self,
        *,
        project: str,
        location: str,
        model: str,
        max_context: int = 1_000_000,
        timeout: float = 60.0,
    ) -> None:
        self._project = project
        self._location = location
        self._model = model
        self._caps = Caps(tool_calling=True, json_mode=True, max_context=max_context)
        self._timeout = timeout
        self._url = (
            f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/"
            f"locations/{location}/publishers/google/models/{model}:generateContent"
        )

    @property
    def caps(self) -> Caps:
        return self._caps

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def is_cloud(self) -> bool:
        return True

    def _token(self) -> str:
        try:
            import google.auth  # type: ignore
            from google.auth.transport.requests import Request  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "Vertex için google-auth gerekli: pip install '.[vertex]'"
            ) from exc
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(Request())
        return creds.token

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
            messages=messages, tools=tools,
            schema=schema if self._caps.json_mode else None,
            temperature=temperature, max_tokens=max_tokens,
        )
        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        data = post_json(self._url, headers=headers, json=payload, timeout=self._timeout)
        return normalize_response(data, self._model)


def build_request(
    *,
    messages: list[Msg],
    tools: list[ToolSpec] | None,
    schema: dict[str, Any] | None,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    contents: list[dict[str, Any]] = []
    system_parts: list[str] = []
    for m in messages:
        if m.role == "system":
            system_parts.append(m.content)
            continue
        role = "model" if m.role == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m.content}]})

    gen_config: dict[str, Any] = {"temperature": temperature, "maxOutputTokens": max_tokens}
    if schema is not None:
        gen_config["responseMimeType"] = "application/json"
        gen_config["responseSchema"] = schema

    payload: dict[str, Any] = {"contents": contents, "generationConfig": gen_config}
    if system_parts:
        payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    if tools:
        payload["tools"] = [
            {"functionDeclarations": [
                {"name": t.name, "description": t.description, "parameters": t.parameters}
                for t in tools
            ]}
        ]
    return payload


def normalize_response(data: dict[str, Any], model: str) -> LLMResponse:
    """Gemini generateContent yanıtı → LLMResponse (saf fonksiyon)."""
    candidate = (data.get("candidates") or [{}])[0]
    parts = (candidate.get("content") or {}).get("parts") or []
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for part in parts:
        if "text" in part:
            text_parts.append(part["text"])
        elif "functionCall" in part:
            fc = part["functionCall"]
            tool_calls.append(ToolCall(id=fc.get("name", ""), name=fc.get("name", ""), arguments=fc.get("args", {})))
    text = "".join(text_parts) or None

    parsed: dict[str, Any] | None = None
    if text:
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                parsed = None

    usage_raw = data.get("usageMetadata") or {}
    return LLMResponse(
        text=text,
        tool_calls=tool_calls,
        parsed=parsed,
        usage=Usage(
            prompt_tokens=usage_raw.get("promptTokenCount", 0),
            completion_tokens=usage_raw.get("candidatesTokenCount", 0),
        ),
        model_id=model,
        finish_reason=candidate.get("finishReason"),
    )
