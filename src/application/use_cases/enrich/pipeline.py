"""EnrichmentPipeline — M4 anlamsal katman (design/06/07/19 bootstrap sırası, kümelemesiz).

enrich → taxonomy (seed+LLM) → categorize → embed/index → catalog/README → re-upsert.
LLM None → enrich/categorize atlanır, taksonomi seed-only, kart yapısal-only embed (design/07 fallback).
"""

from __future__ import annotations

from src.application.use_cases.categorize.categorize_object import CategorizeObject
from src.application.use_cases.enrich.enrich_object import EnrichObject, EnrichTable
from src.application.use_cases.indexing.build_catalog import BuildCatalog
from src.application.use_cases.indexing.index_object import IndexObject, IndexTable
from src.application.use_cases.taxonomy.build_taxonomy import BuildTaxonomy
from src.domain.entities.catalog import CatalogObject, TableDef
from src.infrastructure.settings.config import TaxonomyConfig


class EnrichmentPipeline:
    def __init__(
        self,
        *,
        enrich_object: EnrichObject,
        enrich_table: EnrichTable,
        build_taxonomy: BuildTaxonomy,
        categorize: CategorizeObject,
        index_object: IndexObject,
        index_table: IndexTable,
        build_catalog: BuildCatalog,
        store,
        catalog_repo,
        taxonomy_cfg: TaxonomyConfig,
    ) -> None:
        self._enrich_obj = enrich_object
        self._enrich_table = enrich_table
        self._build_tax = build_taxonomy
        self._categorize = categorize
        self._index_obj = index_object
        self._index_table = index_table
        self._build_catalog = build_catalog
        self._store = store
        self._catalog = catalog_repo
        self._cfg = taxonomy_cfg

    async def run(
        self,
        *,
        server: str,
        database: str,
        code_objects: list[CatalogObject],
        tables: list[TableDef],
        raw_sql_by_uid: dict[str, str | None],
    ) -> None:
        # 1) enrich (özet + tablo/kolon açıklaması, kalite kapısı)
        for obj in code_objects:
            await self._enrich_obj.execute(obj)
        for table in tables:
            await self._enrich_table.execute(table)

        # 2) taksonomi (kod + veri ayrı, seed + LLM etiketleme)
        code_tax = self._build_tax.execute(
            database=database, kind="code", seed=self._cfg.code_seed,
            summaries=[o.summary or o.name for o in code_objects],
            previous=self._store.load_taxonomy(server, database, "code"),
        )
        self._store.write_taxonomy(server, database, code_tax)
        data_tax = self._build_tax.execute(
            database=database, kind="data", seed=self._cfg.data_seed,
            summaries=[t.table_description or t.name for t in tables],
            previous=self._store.load_taxonomy(server, database, "data"),
        )
        self._store.write_taxonomy(server, database, data_tax)

        # 3) categorize (kod ekseni)
        for obj in code_objects:
            await self._categorize.execute(obj, code_tax)

        # 4) embed/index (card + body + table)
        for obj in code_objects:
            await self._index_obj.execute(obj, raw_sql=raw_sql_by_uid.get(obj.uid))
        for table in tables:
            await self._index_table.execute(table)

        # 5) catalog/README (kod taksonomisi)
        self._build_catalog.execute(
            server=server, database=database, taxonomy=code_tax, objects=code_objects
        )

        # 6) zenginleştirilmiş satırları diske + Postgres'e geri yaz
        for obj in code_objects:
            self._store.write_meta(obj)
            await self._catalog.upsert_object(obj)
        for table in tables:
            self._store.write_table(table)
            await self._catalog.upsert_table(table)
