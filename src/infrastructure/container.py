"""Composition Root (design/12, /13) — port→adapter wiring + lifecycle.

CLI ve api/main.py aynı Container'ı kurar. Hiçbir use-case infrastructure'ı doğrudan import etmez.
"""

from __future__ import annotations

from pathlib import Path

from src.application.use_cases.queries import DescribeTable, GetDependencies, ShowObject, Status
from src.application.use_cases.sync.sync_database import SyncDatabase
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

    # --- use-case factories ---------------------------------------------
    def sync_use_case(self, server_id: str) -> SyncDatabase:
        return SyncDatabase(
            source=self.source_for(server_id),
            store=self.store,
            parser=self.parser,
            catalog=self.catalog,
            graph=self.graph,
            runs=self.runs,
            config=self.servers_config,
        )

    def show_use_case(self) -> ShowObject:
        return ShowObject(self.catalog, self.store)

    def deps_use_case(self) -> GetDependencies:
        return GetDependencies(self.catalog, self.graph)

    def table_use_case(self) -> DescribeTable:
        return DescribeTable(self.catalog)

    def status_use_case(self) -> Status:
        return Status(self.catalog, self.runs)

    async def aclose(self) -> None:
        await self.db.dispose()
