"""Cloud embedding (OpenAI text-embedding-3) — dense-only (design/07/09).

`dimensions` parametresiyle çıktı boyutu BGE-M3 ile aynı (1024) tutulur → embeddings.embedding
vector(1024) kolonuyla uyumlu. supports_sparse=False → retrieval dense+trigram'a düşer (design/08).
"""

from __future__ import annotations

from src.application.dtos.llm import EmbedResult
from src.infrastructure.llm.base import post_json


class OpenAIEmbedding:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "text-embedding-3-small",
        dim: int = 1024,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._dim = dim
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._timeout = timeout

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def supports_sparse(self) -> bool:
        return False

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def is_cloud(self) -> bool:
        return True

    def embed(self, texts: list[str], kind: str = "passage") -> list[EmbedResult]:
        data = post_json(
            self._url,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            json={"model": self._model, "input": texts, "dimensions": self._dim},
            timeout=self._timeout,
        )
        rows = sorted(data.get("data", []), key=lambda r: r.get("index", 0))
        return [EmbedResult(dense=[float(x) for x in r["embedding"]], sparse=None) for r in rows]
