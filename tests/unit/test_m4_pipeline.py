"""M4 enrich→taxonomy→categorize→index→catalog — sahte LLM/embedding ile uçtan uca (Docker'sız)."""

import json

import pytest

from src.application.dtos.llm import Caps, EmbedResult, LLMResponse
from src.application.use_cases.categorize.categorize_object import CategorizeObject
from src.application.use_cases.enrich.enrich_object import EnrichObject, EnrichTable
from src.application.use_cases.enrich.pipeline import EnrichmentPipeline
from src.application.use_cases.indexing.build_catalog import BuildCatalog
from src.application.use_cases.indexing.index_object import IndexObject, IndexTable
from src.application.use_cases.taxonomy.build_taxonomy import BuildTaxonomy
from src.domain.entities.catalog import CatalogObject, Column, Parameter, TableDef, TableRef
from src.infrastructure.settings.config import TaxonomyConfig
from src.infrastructure.store.disk_store import DiskObjectStore


class FakeLLM:
    """Sistem mesajına göre uygun parsed JSON döndürür (structured() bunu doğrular)."""

    caps = Caps(tool_calling=False, json_mode=False, max_context=8192)
    model_id = "fake-llm"
    is_cloud = False

    def chat(self, messages, **kw):
        system = messages[0].content if messages else ""
        if "katalog asistanı" in system:  # enrich object
            return LLMResponse(parsed={"summary": "dbo.TEKLIF üzerinden teklif sürelerini hesaplar"})
        if "veri sözlüğü" in system:  # enrich table
            return LLMResponse(parsed={"table_description": "Teklif kayıtlarını tutar", "columns": {}})
        if "taksonomist" in system:  # taxonomy
            return LLMResponse(parsed={"categories": [{"key": "teklif", "label": "Teklif"}]})
        if "sınıflandırıcı" in system:  # categorize
            return LLMResponse(parsed={"category": "teklif", "secondary": [], "confidence": 0.95})
        return LLMResponse(text="ok")


class FakeEmbedding:
    dim = 8
    supports_sparse = False
    model_id = "fake-emb"
    is_cloud = False

    def embed(self, texts, kind="passage"):
        return [EmbedResult(dense=[0.1] * 8, sparse=None) for _ in texts]


class FakeEmbRepo:
    def __init__(self):
        self.written = []  # (uid, kind)

    async def replace(self, uid, kind, content, result, *, model_id, dim):
        self.written.append((uid, kind))

    async def replace_many(self, uid, kind, items, *, model_id, dim):
        self.written.extend((uid, kind) for _ in items)


class FakeCatalogRepo:
    def __init__(self):
        self.objects = {}

    async def upsert_object(self, obj):
        self.objects[obj.uid] = obj

    async def upsert_table(self, table):
        self.objects[table.uid] = table


def _sp():
    o = CatalogObject(uid="demo/DemoDB/10", alias="demo/DemoDB/dbo/SP_TEKLIF", server="demo",
                      database="DemoDB", schema="dbo", name="SP_TEKLIF", type="procedure", object_id=10)
    o.parameters = [Parameter(name="@KullaniciID", type="INT")]
    o.reads_tables = [TableRef(name="dbo.TEKLIF")]
    o.content_hash = "sha256:abc"
    return o


def _table():
    t = TableDef(uid="demo/DemoDB/20", alias="demo/DemoDB/dbo/TEKLIF", server="demo",
                 database="DemoDB", schema="dbo", name="TEKLIF", object_id=20)
    t.columns = [Column(name="TeklifNo", type="INT", pk=True)]
    return t


@pytest.mark.asyncio
async def test_enrich_human_precedence():
    obj = _sp()
    obj.human_description = "İnsan açıklaması"
    await EnrichObject(FakeLLM()).execute(obj)
    assert obj.summary is None  # human-önce: LLM çağrılmaz, summary boş kalır (kart human_desc kullanır)
    assert obj.summary_confidence == "ok"


@pytest.mark.asyncio
async def test_enrich_quality_gate_rejects_hallucination():
    class HallucLLM(FakeLLM):
        def chat(self, messages, **kw):
            return LLMResponse(parsed={"summary": "UYDURMA_TABLO tablosunu kullanır"})

    obj = _sp()
    await EnrichObject(HallucLLM()).execute(obj)
    assert obj.summary is None and obj.summary_confidence == "low"  # zehirli özet reddedildi


@pytest.mark.asyncio
async def test_categorize_pinned_override():
    obj = _sp()
    obj.pinned = True
    obj.pinned_category = "police"
    from src.domain.entities.taxonomy import Taxonomy

    tax = Taxonomy.from_seed("DemoDB", "code", ["teklif", "police"])
    await CategorizeObject(FakeLLM()).execute(obj, tax)
    assert obj.category == "police"  # LLM dokunmaz


@pytest.mark.asyncio
async def test_full_pipeline(tmp_path):
    store = DiskObjectStore(tmp_path)
    llm, emb = FakeLLM(), FakeEmbedding()
    emb_repo, cat_repo = FakeEmbRepo(), FakeCatalogRepo()
    pipeline = EnrichmentPipeline(
        enrich_object=EnrichObject(llm),
        enrich_table=EnrichTable(llm),
        build_taxonomy=BuildTaxonomy(llm),
        categorize=CategorizeObject(llm),
        index_object=IndexObject(emb, emb_repo),
        index_table=IndexTable(emb, emb_repo),
        build_catalog=BuildCatalog(store, None),
        store=store,
        catalog_repo=cat_repo,
        taxonomy_cfg=TaxonomyConfig(code_seed=["teklif"], data_seed=["teklif-verisi"]),
    )
    obj, table = _sp(), _table()
    await pipeline.run(server="demo", database="DemoDB", code_objects=[obj], tables=[table],
                       raw_sql_by_uid={obj.uid: "SELECT 1"})

    # enrich + categorize
    assert obj.summary_confidence == "ok"
    assert obj.category == "teklif"
    assert obj.search_name == "sp_teklif"
    # embeddings: card + table
    assert ("demo/DemoDB/10", "card") in emb_repo.written
    assert ("demo/DemoDB/20", "table") in emb_repo.written
    # taksonomi + catalog disk çıktıları
    assert (tmp_path / "demo" / "DemoDB" / "catalog" / "code" / "_taxonomy.json").exists()
    cat_json = tmp_path / "demo" / "DemoDB" / "catalog" / "code" / "teklif" / "catalog.json"
    assert cat_json.exists()
    data = json.loads(cat_json.read_text(encoding="utf-8"))
    assert data["object_count"] == 1
    assert (tmp_path / "demo" / "DemoDB" / "catalog" / "code" / "teklif" / "README.md").exists()
