from src.domain.entities.catalog import CatalogObject
from src.infrastructure.store.disk_store import DiskObjectStore


def _obj():
    return CatalogObject(
        uid="demo/DemoDB/1",
        alias="demo/DemoDB/dbo/SP_X",
        server="demo",
        database="DemoDB",
        schema="dbo",
        name="SP_X",
        type="procedure",
        object_id=1,
    )


def test_write_and_read_definition(tmp_path):
    store = DiskObjectStore(tmp_path)
    obj = _obj()
    store.write_definition(obj, "SELECT 1")
    assert store.read_definition(obj) == "SELECT 1"


def test_keep_prev_on_change(tmp_path):
    store = DiskObjectStore(tmp_path)
    obj = _obj()
    store.write_definition(obj, "SELECT 1", keep_prev=True)
    store.write_definition(obj, "SELECT 2", keep_prev=True)
    prev = tmp_path / "demo" / "DemoDB" / "procedures" / "dbo" / "SP_X.prev.sql"
    assert prev.exists() and prev.read_text(encoding="utf-8") == "SELECT 1"
    assert store.read_definition(obj) == "SELECT 2"


def test_manifest_roundtrip(tmp_path):
    from src.domain.entities.manifest import InventoryItem, Manifest

    store = DiskObjectStore(tmp_path)
    m = Manifest(
        server="demo",
        database="DemoDB",
        objects=[InventoryItem(schema="dbo", name="SP_X", type="procedure", object_id=1)],
    )
    store.save_manifest(m)
    loaded = store.load_manifest("demo", "DemoDB")
    assert loaded is not None
    assert loaded.objects[0].name == "SP_X"


def test_filename_sanitize_keeps_real_name_in_meta(tmp_path):
    store = DiskObjectStore(tmp_path)
    obj = _obj()
    obj.name = "SP/Weird:Name"
    store.write_meta(obj)
    # dosya adı sanitize, ama meta içinde gerçek ad
    import json

    meta_dir = tmp_path / "demo" / "DemoDB" / "procedures" / "dbo"
    files = list(meta_dir.glob("*.meta.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["name"] == "SP/Weird:Name"
