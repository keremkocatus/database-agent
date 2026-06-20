"""Composition Root (design/12, /13) — port→adapter wiring + lifecycle.

CLI ve api/main.py aynı Container'ı kurar. Hiçbir use-case infrastructure'ı doğrudan import etmez.
"""

from __future__ import annotations

from pathlib import Path

from src.application.ports.embedding import EmbeddingProvider
from src.application.ports.llm import LLMProvider
from src.application.use_cases.queries import DescribeTable, GetDependencies, ShowObject, Status
from src.application.use_cases.sync.sync_database import SyncDatabase
from src.infrastructure.llm.factory import build_chat, build_embedding
from src.infrastructure.parsing.sqlglot_parser import SqlglotParser
from src.infrastructure.persistence.database_client import DatabaseClient
from src.infrastructure.persistence.migrations.runner import MigrationRunner
from src.infrastructure.persistence.repositories.catalog_repo import CatalogRepo
from src.infrastructure.persistence.repositories.graph_repo import GraphRepo
from src.infrastructure.persistence.repositories.runs_repo import RunsRepo
from src.infrastructure.settings.config import Settings, ServersConfig, load_servers_config
from src.infrastructure.source.mssql.adapter import MssqlAdapter
from src.infrastructure.store.disk_store import DiskObjectStore

_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


class Container:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self.db = DatabaseClient(self.settings.database_url)
        self.store = DiskObjectStore(self.settings.data_dir)
        self.parser = SqlglotParser()
        self.catalog = CatalogRepo(self.db)
        self.graph = GraphRepo(self.db)
        self.runs = RunsRepo(self.db)
        self.migrations = MigrationRunner(self.db, _MIGRATIONS_DIR)
        self._servers_config: ServersConfig | None = None

    # --- config ----------------------------------------------------------
    @property
    def servers_config(self) -> ServersConfig:
        if self._servers_config is None:
            self._servers_config = load_servers_config(self.settings.config_path)
        return self._servers_config

    def source_for(self, server_id: str) -> MssqlAdapter:
        cfg = self.servers_config
        return MssqlAdapter(cfg.server(server_id), cfg.defaults)

    # --- provider katmanı (M3, design/09) -------------------------------
    def llm_provider(self, role: str | None = None, server_id: str | None = None) -> LLMProvider:
        cfg = self.servers_config
        return build_chat(cfg.llm.for_role(role), allow_cloud=cfg.cloud_allowed(server_id))

    def embedding_provider(self, server_id: str | None = None) -> EmbeddingProvider:
        cfg = self.servers_config
        return build_embedding(cfg.embedding, allow_cloud=cfg.cloud_allowed(server_id))

    # --- use-case factories ---------------------------------------------
    def sync_use_case(self, server_id: str, *, enable_llm: bool = True) -> SyncDatabase:
        enrichment = self._build_enrichment(server_id) if enable_llm else None
        return SyncDatabase(
            source=self.source_for(server_id),
            store=self.store,
            parser=self.parser,
            catalog=self.catalog,
            graph=self.graph,
            runs=self.runs,
            config=self.servers_config,
            enrichment=enrichment,
        )

    def _build_enrichment(self, server_id: str):
        """M4 pipeline'ı kur — embedding/LLM uygunsa; ikisi de yoksa None (M2 davranışı)."""
        from importlib.util import find_spec

        from src.application.llm.cache import NullCache
        from src.application.use_cases.categorize.categorize_object import CategorizeObject
        from src.application.use_cases.enrich.enrich_object import EnrichObject, EnrichTable
        from src.application.use_cases.enrich.pipeline import EnrichmentPipeline
        from src.application.use_cases.indexing.build_catalog import BuildCatalog
        from src.application.use_cases.indexing.index_object import IndexObject, IndexTable
        from src.application.use_cases.taxonomy.build_taxonomy import BuildTaxonomy
        from src.infrastructure.persistence.repositories.embeddings_repo import EmbeddingsRepo
        from src.infrastructure.persistence.repositories.llm_cache_repo import LlmCacheRepo

        embedding = None
        try:
            provider = self.embedding_provider(server_id)
            # Lokal BGE-M3: FlagEmbedding kurulu değilse devre dışı (cloud her zaman uygun).
            if provider.is_cloud or find_spec("FlagEmbedding") is not None:
                embedding = provider
        except Exception:
            embedding = None

        llm = None
        try:
            llm = self.llm_provider(role="enricher", server_id=server_id)
        except Exception:
            llm = None

        if embedding is None and llm is None:
            return None  # sağlayıcı yok → yapısal-only (M2)

        cache = LlmCacheRepo(self.db) if self.servers_config.llm.cache.offline_tasks else NullCache()
        emb_repo = EmbeddingsRepo(self.db)
        cat_llm = None
        try:
            cat_llm = self.llm_provider(role="categorizer", server_id=server_id)
        except Exception:
            cat_llm = llm

        return EnrichmentPipeline(
            enrich_object=EnrichObject(llm, cache),
            enrich_table=EnrichTable(llm, cache),
            build_taxonomy=BuildTaxonomy(llm),
            categorize=CategorizeObject(cat_llm, cache),
            index_object=IndexObject(embedding, emb_repo),
            index_table=IndexTable(embedding, emb_repo),
            build_catalog=BuildCatalog(self.store, llm),
            store=self.store,
            catalog_repo=self.catalog,
            taxonomy_cfg=self.servers_config.taxonomy,
        )

    def embeddings_repo(self):
        from src.infrastructure.persistence.repositories.embeddings_repo import EmbeddingsRepo
        return EmbeddingsRepo(self.db)

    def show_use_case(self) -> ShowObject:
        return ShowObject(self.catalog, self.store)

    def deps_use_case(self) -> GetDependencies:
        return GetDependencies(self.catalog, self.graph)

    def table_use_case(self) -> DescribeTable:
        return DescribeTable(self.catalog)

    def status_use_case(self) -> Status:
        return Status(self.catalog, self.runs, self.embeddings_repo())

    async def aclose(self) -> None:
        await self.db.dispose()
