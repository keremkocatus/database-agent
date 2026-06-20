"""BGE-M3 lokal embedding (design/07 varsayılan) — dense(1024) + öğrenilmiş sparse.

FlagEmbedding + torch **opsiyonel extra** [local]; lazy-load tek instance (design/09 kaynak yönetimi).
Kurulu değilse anlamlı hata → cloud embedding'e geçilebilir.
"""

from __future__ import annotations

from src.application.dtos.llm import EmbedResult


class BgeM3Embedding:
    def __init__(self, *, model: str = "BAAI/bge-m3", dim: int = 1024, use_fp16: bool = False) -> None:
        self._model_name = model
        self._dim = dim
        self._use_fp16 = use_fp16
        self._model = None  # lazy

    def _ensure(self):
        if self._model is None:
            try:
                from FlagEmbedding import BGEM3FlagModel  # type: ignore
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "BGE-M3 için FlagEmbedding gerekli: pip install '.[local]' "
                    "(veya config'te embedding.provider: cloud)"
                ) from exc
            self._model = BGEM3FlagModel(self._model_name, use_fp16=self._use_fp16)
        return self._model

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def supports_sparse(self) -> bool:
        return True

    @property
    def model_id(self) -> str:
        return "bge-m3"

    @property
    def is_cloud(self) -> bool:
        return False

    def embed(self, texts: list[str], kind: str = "passage") -> list[EmbedResult]:
        model = self._ensure()
        out = model.encode(texts, return_dense=True, return_sparse=True, return_colbert_vecs=False)
        dense = out["dense_vecs"]
        lexical = out["lexical_weights"]
        results: list[EmbedResult] = []
        for i in range(len(texts)):
            sparse = {int(tok): float(w) for tok, w in lexical[i].items()}
            results.append(EmbedResult(dense=[float(x) for x in dense[i]], sparse=sparse))
        return results
