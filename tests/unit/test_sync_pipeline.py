"""SyncDatabase entegrasyon testi — gerçek parser + gerçek disk store + in-memory fake'ler.

Docker (Postgres/MSSQL) olmadan M1+M2 orkestrasyonunu uçtan uca doğrular:
extract→parse→tablo sözlüğü→kenar kurulumu + değişim tespiti (ALTER → changed + .prev.sql).
Postgres'e özgü SQL (recursive CTE, migration) docker doğrulamasına bırakılır.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.application.dtos.source import (
    ColumnInfo,
    ForeignKeyInfo,
    RawDefinition,
    ServerSideDependency,
    TableSchema,
)
from src.domain.entities.catalog import DependencyEdge
from src.domain.entities.manifest import InventoryItem, SynonymItem
from src.infrastructure.parsing.sqlglot_parser import SqlglotParser
from src.infrastructure.settings.config import ServersConfig
from src.infrastructure.store.disk_store import DiskObjectStore
from src.application.use_cases.sync.sync_database import SyncDatabase

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

SP_MAIN_V1 = """
CREATE PROCEDURE dbo.SP_MAIN @KullaniciID INT AS
BEGIN
    EXEC dbo.SP_SUB @KullaniciID;
    INSERT INTO dbo.TEKLIF_LOG (TeklifNo) SELECT TeklifNo FROM dbo.TEKLIF WHERE KullaniciID=@KullaniciID;
END
"""
SP_MAIN_V2 = SP_MAIN_V1 + "\n-- v2 degisiklik\n"
SP_SUB = "CREATE PROCEDURE dbo.SP_SUB @KullaniciID INT AS BEGIN SELECT 1; END"


class FakeSource:
    """SourceDbPort fake — demo nesneleri + ALTER senaryosu (sürüm parametresi)."""

    def __init__(self) -> None:
        self.sp_main_sql = SP_MAIN_V1
        self.sp_main_modify = T0

    def inventory_objects(self, server, database):
        return [
            InventoryItem("dbo", "SP_MAIN", "procedure", 10, self.sp_main_modify),
            InventoryItem("dbo", "SP_SUB", "procedure", 11, T0),
            InventoryItem("dbo", "TEKLIF", "table", 20, T0),
            InventoryItem("dbo", "TEKLIF_LOG", "table", 21, T0),
        ]

    def list_synonyms(self, server, database):
        return [SynonymItem("dbo", "TEKLIF_SYN", "DemoDB.dbo.TEKLIF", False)]

    def fetch_definitions(self, server, database):
        return {
            10: RawDefinition(10, "dbo", "SP_MAIN", "procedure", self.sp_main_modify, self.sp_main_sql),
            11: RawDefinition(11, "dbo", "SP_SUB", "procedure", T0, SP_SUB),
        }

    def fetch_dependencies(self, server, database):
        return [
            ServerSideDependency(10, None, "dbo", "SP_SUB", None, False),     # calls
            ServerSideDependency(10, None, "dbo", "TEKLIF", None, False),     # reads
            ServerSideDependency(10, None, "dbo", "TEKLIF_LOG", None, True),  # writes
        ]

    def fetch_table_schemas(self, server, database):
        return {
            20: TableSchema(
                20, "dbo", "TEKLIF", "table",
                columns=[
                    ColumnInfo("TeklifNo", "int", False, "int", False, True, None, None, None),
                    ColumnInfo("KullaniciID", "int", False, "int", False, False, None, None, None),
                    ColumnInfo("Sure", "int", False, "int", True, False, None, None, None, "Teklif süresi (gün)"),
                ],
                primary_key=["TeklifNo"],
                foreign_keys=[ForeignKeyInfo("FK_T_K", "KullaniciID", "dbo.KULLANICI", "ID")],
                check_constraints=[{"name": "CK_Durum", "definition": "[Durum] IN ('A','P','I')"}],
            ),
            21: TableSchema(21, "dbo", "TEKLIF_LOG", "table",
                            columns=[ColumnInfo("LogID", "int", False, "int", False, True, None, None, None)],
                            primary_key=["LogID"]),
        }

    def discover_databases(self, server):
        return ["DemoDB"]

    def probe(self, server):
        return True, "fake"


class InMemoryCatalog:
    def __init__(self) -> None:
        self.objects: dict[str, dict] = {}
        self.edges: dict[str, list[DependencyEdge]] = {}

    async def upsert_object(self, obj):
        self.objects[obj.uid] = {"uid": obj.uid, "alias": obj.alias, "type": obj.type,
                                 "name": obj.name, "state": obj.state, "hash": obj.content_hash}

    async def upsert_table(self, table):
        self.objects[table.uid] = {"uid": table.uid, "alias": table.alias, "type": "table",
                                   "name": table.name, "object_kind": table.object_kind}

    async def replace_edges(self, src_uid, edges):
        # Gerçek repo gibi: yalnızca hedefi var olan kenarlar (FK/kapsam kuralı).
        self.edges[src_uid] = [e for e in edges if e.dst_uid in self.objects]

    async def remove_object(self, uid):
        self.objects.pop(uid, None)
        self.edges.pop(uid, None)

    def all_edges(self):
        return [e for lst in self.edges.values() for e in lst]


class InMemoryGraph:
    pass


class InMemoryRuns:
    def __init__(self) -> None:
        self.finished: list[tuple] = []

    async def start_run(self, server, database):
        return "run-1"

    async def finish_run(self, run_id, status, counts, errors):
        self.finished.append((status, counts, errors))

    async def recent(self, limit=10):
        return []


def _make(tmp_path):
    catalog = InMemoryCatalog()
    runs = InMemoryRuns()
    source = FakeSource()
    uc = SyncDatabase(
        source=source,
        store=DiskObjectStore(tmp_path),
        parser=SqlglotParser(),
        catalog=catalog,
        graph=InMemoryGraph(),
        runs=runs,
        config=ServersConfig(),
    )
    return uc, source, catalog, runs


@pytest.mark.asyncio
async def test_first_sync_populates_disk_and_catalog(tmp_path):
    uc, source, catalog, runs = _make(tmp_path)
    summary = await uc.execute("demo", "DemoDB")

    assert summary.status == "ok"
    assert summary.counts["added"] == 4  # 2 SP + 2 tablo
    # Disk: ham SQL + meta
    assert (tmp_path / "demo" / "DemoDB" / "procedures" / "dbo" / "SP_MAIN.sql").exists()
    assert (tmp_path / "demo" / "DemoDB" / "tables" / "dbo" / "TEKLIF.json").exists()
    assert (tmp_path / "demo" / "DemoDB" / "_manifest.json").exists()
    # Katalog: 4 nesne
    assert len(catalog.objects) == 4


@pytest.mark.asyncio
async def test_edges_calls_reads_writes(tmp_path):
    uc, source, catalog, runs = _make(tmp_path)
    await uc.execute("demo", "DemoDB")
    edges = {(e.dst_uid.split("/")[-1], e.kind) for e in catalog.all_edges()}
    assert ("11", "calls") in edges     # SP_MAIN → SP_SUB
    assert ("20", "reads") in edges      # SP_MAIN → TEKLIF
    assert ("21", "writes") in edges     # SP_MAIN → TEKLIF_LOG


@pytest.mark.asyncio
async def test_second_sync_unchanged_then_alter_detected(tmp_path):
    uc, source, catalog, runs = _make(tmp_path)
    await uc.execute("demo", "DemoDB")

    # İkinci sync: değişiklik yok → changed=0
    s2 = await uc.execute("demo", "DemoDB")
    assert s2.counts["changed"] == 0
    assert s2.counts["unchanged"] >= 2

    # ALTER: SP_MAIN içeriği + modify_date değişti
    source.sp_main_sql = SP_MAIN_V2
    source.sp_main_modify = datetime(2026, 2, 1, tzinfo=timezone.utc)
    s3 = await uc.execute("demo", "DemoDB")
    assert s3.counts["changed"] == 1
    # .prev.sql yazıldı + changelog olayı
    assert (tmp_path / "demo" / "DemoDB" / "procedures" / "dbo" / "SP_MAIN.prev.sql").exists()
    changelog = (tmp_path / "demo" / "DemoDB" / "_changelog.jsonl").read_text(encoding="utf-8")
    assert '"kind": "changed"' in changelog


@pytest.mark.asyncio
async def test_table_dictionary_columns_and_fk(tmp_path):
    uc, source, catalog, runs = _make(tmp_path)
    await uc.execute("demo", "DemoDB")
    import json

    teklif = json.loads(
        (tmp_path / "demo" / "DemoDB" / "tables" / "dbo" / "TEKLIF.json").read_text(encoding="utf-8")
    )
    assert teklif["primary_key"] == ["TeklifNo"]
    assert any(fk["to_table"] == "dbo.KULLANICI" for fk in teklif["foreign_keys"])
    sure = next(c for c in teklif["columns"] if c["name"] == "Sure")
    assert sure["human_description"] == "Teklif süresi (gün)"  # extended property otoriter
    # read/write ayrımı: TEKLIF_LOG'u yazan SP_MAIN görünür
    log = json.loads(
        (tmp_path / "demo" / "DemoDB" / "tables" / "dbo" / "TEKLIF_LOG.json").read_text(encoding="utf-8")
    )
    assert any(uid.endswith("/10") for uid in log["written_by_objects"])
