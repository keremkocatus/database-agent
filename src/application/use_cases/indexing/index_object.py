"""IndexObject / IndexTable / IndexCategory — kart → embeddings (design/07).

embedding provider yoksa hiç embed yok (M2 davranışı; nesne yapısal kalır). Varsa: özet olsun
olmasın kart embed edilir (yapısal-only fallback → nesne aramada görünmez kalmaz, design/07).
"""

from __future__ import annotations

from src.application.dtos.llm import EmbedResult
from src.application.ports.embedding import EmbeddingProvider
from src.domain.entities.catalog import CatalogObject, TableDef
from src.domain.services.card_builder import build_object_card, build_table_card
from src.domain.services.turkish_fold import turkish_fold

_BODY_THRESHOLD_LINES = 300


class IndexObject:
    def __init__(self, embedding: EmbeddingProvider | None, repo) -> None:
        self._embedding = embedding
        self._repo = repo

    async def execute(self, obj: CatalogObject, *, raw_sql: str | None = None) -> None:
        obj.search_name = turkish_fold(obj.name)
        if self._embedding is None:
            return  # embedding yok → yapısal kalır (M2)

        card = build_object_card(obj)
        vec = self._embedding.embed([card], kind="passage")[0]
        await self._repo.replace(obj.uid, "card", card, vec,
                                 model_id=self._embedding.model_id, dim=self._embedding.dim)

        # Büyük nesne → gövde chunk'ları (design/07 ikincil temsil).
        if raw_sql and obj.loc > _BODY_THRESHOLD_LINES:
            chunks = _chunk_sql(raw_sql)
            if chunks:
                vecs = self._embedding.embed(chunks, kind="passage")
                items = list(zip(chunks, vecs))
                await self._repo.replace_many(obj.uid, "body", items,
                                              model_id=self._embedding.model_id, dim=self._embedding.dim)
        obj.state = "indexed"


class IndexTable:
    def __init__(self, embedding: EmbeddingProvider | None, repo) -> None:
        self._embedding = embedding
        self._repo = repo

    async def execute(self, table: TableDef) -> None:
        if self._embedding is None:
            return
        card = build_table_card(table)
        vec = self._embedding.embed([card], kind="passage")[0]
        await self._repo.replace(table.uid, "table", card, vec,
                                 model_id=self._embedding.model_id, dim=self._embedding.dim)


def _chunk_sql(sql: str, max_lines: int = 120) -> list[str]:
    """Basit blok bölme — sqlglot statement split, aşırı uzunsa satır penceresi."""
    try:
        import sqlglot

        stmts = [s.sql(dialect="tsql") for s in sqlglot.parse(sql, dialect="tsql") if s]
    except Exception:
        stmts = []
    if not stmts:
        lines = sql.splitlines()
        return ["\n".join(lines[i : i + max_lines]) for i in range(0, len(lines), max_lines)]
    return stmts
