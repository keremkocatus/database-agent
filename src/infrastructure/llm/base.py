"""Chat adapter ortak temeli — HTTP retry/backoff/timeout (design/09 dayanıklılık)."""

from __future__ import annotations

import time
from typing import Any

import httpx


class LLMError(RuntimeError):
    """Provider çağrısı kalıcı başarısızlığı."""


def post_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: dict[str, Any],
    timeout: float = 60.0,
    retries: int = 2,
    backoff: float = 2.0,
) -> dict[str, Any]:
    """POST + JSON; geçici hata/timeout'ta 2 deneme, üstel backoff (design/09)."""
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = httpx.post(url, headers=headers or {}, json=json, timeout=timeout)
            if resp.status_code >= 500 or resp.status_code == 429:
                raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            resp.raise_for_status()
            return resp.json()
        except (httpx.TimeoutException, httpx.TransportError, LLMError) as exc:
            last = exc
            if attempt < retries:
                time.sleep(backoff * (2**attempt))
        except httpx.HTTPStatusError as exc:
            # 4xx (429 hariç) → kalıcı, retry etme.
            raise LLMError(f"HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
    raise LLMError(f"Provider çağrısı başarısız ({retries + 1} deneme): {last}")
