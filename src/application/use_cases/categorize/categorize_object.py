"""CategorizeObject — birincil + ikincil kategori (design/06).

- Pinned override: pinned_category doluysa LLM dokunmaz (insan kürasyonu kalıcı).
- Önbellek anahtarı (content_hash + taxonomy_version) → koşular arası titreme (flapping) yok.
- Enum-kısıtlı: yalnızca taksonomi key'leri; uymayan/düşük güven → 'diger'.
- LLM yok → 'diger' (taksonomi varsa) / None.
"""

from __future__ import annotations

from src.application.dtos.llm import Msg
from src.application.llm.cache import LlmCachePort, NullCache, cache_key
from src.application.llm.structured import structured
from src.application.ports.llm import LLMProvider
from src.application.use_cases.enrich.schemas import CategoryOut
from src.domain.entities.catalog import CatalogObject
from src.domain.entities.taxonomy import Taxonomy

_MIN_CONFIDENCE = 0.4


class CategorizeObject:
    def __init__(self, llm: LLMProvider | None, cache: LlmCachePort | None = None) -> None:
        self._llm = llm
        self._cache = cache or NullCache()

    async def execute(self, obj: CatalogObject, taxonomy: Taxonomy) -> None:
        # Pinned: insan kürasyonu korunur.
        if obj.pinned and obj.pinned_category:
            obj.category = obj.pinned_category
            return

        keys = taxonomy.keys()
        if self._llm is None:
            obj.category = "diger" if keys else None
            return

        messages = _prompt(obj, taxonomy)
        key = cache_key("categorizer", self._llm.model_id, messages,
                        extra=f"{obj.content_hash}:{taxonomy.version}")
        cached = await self._cache.get(key)
        out = CategoryOut(**cached) if cached else structured(self._llm, messages, CategoryOut)
        if out and not cached:
            await self._cache.put(key, self._llm.model_id, out.model_dump())

        if out is None or out.confidence < _MIN_CONFIDENCE or out.category not in keys:
            obj.category = "diger"
            return
        obj.category = out.category
        obj.secondary_categories = [s for s in out.secondary if s in keys and s != out.category]


def _prompt(obj: CatalogObject, taxonomy: Taxonomy) -> list[Msg]:
    cats = "\n".join(f"- {c.key}: {c.label}" for c in taxonomy.categories)
    summary = obj.human_description or obj.summary or "(özet yok)"
    tables = ", ".join(t.name for t in obj.reads_tables + obj.writes_tables) or "(yok)"
    user = (
        f"Nesne: {obj.schema}.{obj.name} ({obj.type})\nÖzet: {summary}\nTablolar: {tables}\n\n"
        f"Kategoriler:\n{cats}\n\n"
        f"Bu nesne için BİR birincil 'category' key'i (yukarıdakilerden) ve 0+ 'secondary' key seç. "
        f"Hiçbiri uymuyorsa 'diger'. confidence 0-1 ver."
    )
    return [
        Msg("system", "Sen bir MSSQL katalog sınıflandırıcısısın. Yalnızca verilen key'leri kullanırsın."),
        Msg("user", user),
    ]
