"""BuildTaxonomy — seed + LLM etiketleme (design/06; kümeleme yok).

Tohum kategoriler (config) + LLM ile korpus özetlerinden kategori adı/açıklama rafine. Mevcut taksonomi
varsa version korunur/artar (stabilizasyon). LLM yoksa yalnızca seed (+ diger).
"""

from __future__ import annotations

from src.application.dtos.llm import Msg
from src.application.llm.structured import structured
from src.application.ports.llm import LLMProvider
from src.application.use_cases.enrich.schemas import TaxonomyOut
from src.domain.entities.taxonomy import Category, Taxonomy


class BuildTaxonomy:
    def __init__(self, llm: LLMProvider | None) -> None:
        self._llm = llm

    def execute(
        self,
        *,
        database: str,
        kind: str,  # "code" | "data"
        seed: list[str],
        summaries: list[str],
        previous: Taxonomy | None = None,
    ) -> Taxonomy:
        tax = Taxonomy.from_seed(database, kind, seed)

        if self._llm is not None and summaries:
            proposed = self._label(kind, summaries)
            existing = {c.key for c in tax.categories}
            for item in proposed:
                if item.key and item.key not in existing:
                    tax.categories.append(
                        Category(key=item.key, label=item.label or item.key,
                                 description=item.description, subcategories=item.subcategories)
                    )
                    existing.add(item.key)
                elif item.key in existing and item.description:
                    for c in tax.categories:
                        if c.key == item.key and not c.description:
                            c.description = item.description

        tax.ensure_diger()
        if previous is not None:
            # İçerik değiştiyse version artır (stabilizasyon: mevcut üyelik korunur).
            tax.version = previous.version + (1 if _changed(previous, tax) else 0)
            if tax.version == previous.version:
                tax.version = previous.version
        return tax

    def _label(self, kind: str, summaries: list[str]):
        sample = "\n".join(f"- {s}" for s in summaries[:60] if s)
        what = "kod nesnelerini (SP/View/Function)" if kind == "code" else "tabloların veri alanlarını"
        messages = [
            Msg("system", "Sen bir MSSQL katalog taksonomisti. İş-alanı kategorileri öneren bir asistansın."),
            Msg("user",
                f"Aşağıdaki {what} işlevlerine göre 4-8 iş kategorisine ayır. Her kategori için "
                f"kısa key (kebab-case), label ve 1 cümle açıklama ver.\n\nÖzetler:\n{sample}"),
        ]
        out = structured(self._llm, messages, TaxonomyOut)
        return out.categories if out else []


def _changed(prev: Taxonomy, cur: Taxonomy) -> bool:
    return {c.key for c in prev.categories} != {c.key for c in cur.categories}
