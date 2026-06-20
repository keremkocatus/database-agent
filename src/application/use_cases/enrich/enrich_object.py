"""EnrichObject / EnrichTable — LLM özeti + açıklama, kalite kapısıyla (design/05, /09).

- human-önce: human_description doluysa LLM o alana dokunmaz (design/03/05).
- Kalite kapısı: özette anılan ad yapısal metadata'da yoksa → 1 retry → yine yoksa boş + low (design/05).
- Girdi yapısal/kompakt (ad+param+tablo); ham SQL'e dayanmaz → map-reduce gerekmez (design/07 kart).
- LLM yok / başarısız → summary None (yapısal-only kart, design/07 fallback).
"""

from __future__ import annotations

from src.application.dtos.llm import Msg
from src.application.llm.cache import LlmCachePort, NullCache, cache_key
from src.application.llm.structured import structured
from src.application.ports.llm import LLMProvider
from src.application.use_cases.enrich.schemas import SummaryOut, TableDescOut
from src.domain.entities.catalog import CatalogObject, TableDef
from src.domain.services.quality_gate import collect_identifiers, validate_summary

_STRICT = Msg("user", "Yalnızca verilen tablo/kolon/parametre adlarını kullan; uydurma yapma.")


class EnrichObject:
    def __init__(self, llm: LLMProvider | None, cache: LlmCachePort | None = None) -> None:
        self._llm = llm
        self._cache = cache or NullCache()

    async def execute(self, obj: CatalogObject) -> None:
        # human-önce: otoriter açıklama varsa LLM çağrılmaz.
        if obj.human_description:
            obj.summary_confidence = "ok"
            obj.state = "enriched"
            return
        if self._llm is None:
            obj.summary = None
            obj.summary_confidence = None  # yapısal-only kart
            obj.state = "enriched"
            return

        messages = _object_prompt(obj)
        valid = collect_identifiers(
            tables=[t.name for t in obj.reads_tables + obj.writes_tables],
            columns=[],
            params=[p.name for p in obj.parameters],
        )
        out = await self._cached_or_call(messages, "enricher", SummaryOut)
        if out and validate_summary(out.summary, valid):
            obj.summary, obj.summary_confidence = out.summary, "ok"
        else:
            # 1 retry (daha sıkı), sonra reddet (zehirli özet embeddinge girmez).
            retry = structured(self._llm, messages + [_STRICT], SummaryOut)
            if retry and validate_summary(retry.summary, valid):
                obj.summary, obj.summary_confidence = retry.summary, "ok"
            else:
                obj.summary, obj.summary_confidence = None, "low"
        obj.state = "enriched"

    async def _cached_or_call(self, messages, role, schema):
        key = cache_key(role, self._llm.model_id, messages)
        cached = await self._cache.get(key)
        if cached is not None:
            try:
                return schema(**cached)
            except Exception:
                pass
        out = structured(self._llm, messages, schema)
        if out is not None:
            await self._cache.put(key, self._llm.model_id, out.model_dump())
        return out


class EnrichTable:
    def __init__(self, llm: LLMProvider | None, cache: LlmCachePort | None = None) -> None:
        self._llm = llm
        self._cache = cache or NullCache()

    async def execute(self, table: TableDef) -> None:
        if table.human_description:
            return
        if self._llm is None:
            return
        messages = _table_prompt(table)
        col_names = {c.name for c in table.columns}
        out = structured(self._llm, messages, TableDescOut)
        if out is None:
            return
        # Kalite kapısı: anılan kolonlar tabloda var mı?
        if validate_summary(out.table_description, {c.name for c in table.columns}):
            table.table_description = out.table_description
        for col in table.columns:
            if not col.human_description and col.description is None:
                desc = out.columns.get(col.name)
                if desc and col.name in col_names:
                    col.description = desc


def _object_prompt(obj: CatalogObject) -> list[Msg]:
    params = ", ".join(f"{p.name} {p.type}" for p in obj.parameters) or "(yok)"
    tables = ", ".join(t.name for t in obj.reads_tables + obj.writes_tables) or "(yok)"
    calls = ", ".join(obj.calls_objects) or "(yok)"
    user = (
        f"Veritabanı nesnesi:\n"
        f"Tip: {obj.type}\nAd: {obj.schema}.{obj.name}\n"
        f"Parametreler: {params}\nKullandığı tablolar: {tables}\nÇağırdığı nesneler: {calls}\n\n"
        f"Bu nesnenin ne yaptığını 1-2 cümle Türkçe özetle. Yalnızca verilen adları kullan."
    )
    return [
        Msg("system", "Sen bir MSSQL katalog asistanısın. Kısa, doğru, uydurmasız özet üretirsin."),
        Msg("user", user),
    ]


def _table_prompt(table: TableDef) -> list[Msg]:
    cols = ", ".join(c.name for c in table.columns) or "(yok)"
    checks = "; ".join(c.get("definition", "") for c in table.check_constraints) or "(yok)"
    user = (
        f"Tablo: {table.schema}.{table.name}\nKolonlar: {cols}\n"
        f"Check kuralları: {checks}\n\n"
        f"Bu tablonun ne tuttuğunu 1-2 cümle Türkçe açıkla. Belirsiz kolonlar için kısa açıklama ver."
    )
    return [
        Msg("system", "Sen bir MSSQL veri sözlüğü asistanısın. Uydurma yapmazsın."),
        Msg("user", user),
    ]
