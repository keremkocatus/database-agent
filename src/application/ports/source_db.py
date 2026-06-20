"""Kaynak DB port'u (design/02). MSSQL'e özel her şey bu arayüzün arkasında.

Senkron (pyodbc bloklayıcı). İleride PostgreSQL/Oracle = aynı port'u implemente eden yeni adapter;
çekirdek (parse/index) değişmez.
"""

from __future__ import annotations

from typing import Protocol

from src.application.dtos.source import RawDefinition, ServerSideDependency, TableSchema
from src.domain.entities.manifest import InventoryItem, SynonymItem


class SourceDbPort(Protocol):
    def discover_databases(self, server_id: str) -> list[str]:
        """Erişilebilir, ONLINE, kullanıcı DB'lerinin adları (design/02 keşif sorgusu)."""
        ...

    def inventory_objects(self, server_id: str, database: str) -> list[InventoryItem]:
        """SP/View/Function/Trigger + tablo envanteri (is_ms_shipped=0)."""
        ...

    def list_synonyms(self, server_id: str, database: str) -> list[SynonymItem]:
        ...

    def fetch_definitions(self, server_id: str, database: str) -> dict[int, RawDefinition]:
        """object_id → ham SQL tanımı + bayraklar (tek geçiş sys.sql_modules)."""
        ...

    def fetch_dependencies(self, server_id: str, database: str) -> list[ServerSideDependency]:
        """Bulk sys.sql_expression_dependencies (design/04 birincil bağımlılık kaynağı)."""
        ...

    def fetch_table_schemas(self, server_id: str, database: str) -> dict[int, TableSchema]:
        """Tablo/view şeması: kolon/PK/FK/check/index (design/05)."""
        ...

    def probe(self, server_id: str) -> tuple[bool, str]:
        """doctor için bağlantı/yetki probu → (ok, mesaj)."""
        ...
