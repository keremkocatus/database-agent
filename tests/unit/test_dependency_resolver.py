from src.application.dtos.source import ServerSideDependency
from src.domain.entities.manifest import InventoryItem, SynonymItem
from src.domain.services.dependency_resolver import build_edges


def _inv():
    return [
        InventoryItem(schema="dbo", name="SP_MAIN", type="procedure", object_id=10),
        InventoryItem(schema="dbo", name="SP_SUB", type="procedure", object_id=11),
        InventoryItem(schema="dbo", name="TEKLIF", type="table", object_id=20),
        InventoryItem(schema="dbo", name="TEKLIF_LOG", type="table", object_id=21),
    ]


def test_calls_reads_writes_classification():
    deps = [
        ServerSideDependency(10, None, "dbo", "SP_SUB", None, False),       # calls
        ServerSideDependency(10, None, "dbo", "TEKLIF", None, False),        # reads
        ServerSideDependency(10, None, "dbo", "TEKLIF_LOG", None, True),     # writes
    ]
    edges = build_edges(server="demo", database="DemoDB", inventory=_inv(), dependencies=deps, synonyms=[])
    kinds = {(e.dst_uid.split("/")[-1], e.kind) for e in edges}
    assert ("11", "calls") in kinds
    assert ("20", "reads") in kinds
    assert ("21", "writes") in kinds


def test_cross_db_target_dropped():
    deps = [ServerSideDependency(10, "OtherDB", "dbo", "X", None, False)]
    edges = build_edges(server="demo", database="DemoDB", inventory=_inv(), dependencies=deps, synonyms=[])
    assert edges == []


def test_synonym_resolution():
    syn = [SynonymItem(schema="dbo", name="TEKLIF_SYN", base="DemoDB.dbo.TEKLIF", cross_db=False)]
    deps = [ServerSideDependency(10, None, "dbo", "TEKLIF_SYN", None, False)]
    edges = build_edges(server="demo", database="DemoDB", inventory=_inv(), dependencies=deps, synonyms=syn)
    assert len(edges) == 1
    assert edges[0].via_synonym is True
    assert edges[0].dst_uid.endswith("/20")  # TEKLIF
