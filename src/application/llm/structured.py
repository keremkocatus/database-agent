"""Yapılandırılmış çıktı — JSON-şema + doğrula/retry (design/09).

caps.json_mode varsa şema native dayatılır; yoksa prompt'a şema açıklaması eklenir. Her durumda
pydantic ile doğrulanır; geçersizse 1-2 retry ("yalnızca JSON"); hâlâ başarısızsa None (çağıran
güvenli varsayılana düşer).
"""

from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from src.application.dtos.llm import Msg
from src.application.ports.llm import LLMProvider

T = TypeVar("T", bound=BaseModel)


def structured(
    provider: LLMProvider,
    messages: list[Msg],
    schema_model: type[T],
    *,
    max_retries: int = 2,
    temperature: float = 0.0,
    seed: int | None = None,
    max_tokens: int = 1024,
) -> T | None:
    json_schema = schema_model.model_json_schema()
    msgs = list(messages)
    if not provider.caps.json_mode:
        msgs = msgs + [Msg("user", _schema_hint(json_schema))]

    for attempt in range(max_retries + 1):
        try:
            resp = provider.chat(
                msgs,
                schema=json_schema if provider.caps.json_mode else None,
                temperature=temperature,
                seed=seed,
                max_tokens=max_tokens,
            )
        except Exception:
            # Provider down/timeout → graceful degrade (çağıran güvenli varsayılana düşer).
            return None
        payload = resp.parsed if resp.parsed is not None else _extract_json(resp.text)
        if isinstance(payload, dict):
            try:
                return schema_model.model_validate(payload)
            except ValidationError:
                pass
        if attempt < max_retries:
            msgs = msgs + [Msg("user", "Önceki yanıt geçersizdi. YALNIZCA şemaya uyan geçerli JSON döndür.")]
    return None


def _schema_hint(json_schema: dict[str, Any]) -> str:
    return (
        "Yanıtını YALNIZCA aşağıdaki JSON şemasına uyan tek bir JSON nesnesi olarak ver "
        "(açıklama/markdown yok):\n" + json.dumps(json_schema, ensure_ascii=False)
    )


def _extract_json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    stripped = text.strip()
    # ```json ... ``` çitlerini soy.
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        result = json.loads(stripped[start : end + 1])
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None
