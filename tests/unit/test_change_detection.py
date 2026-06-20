from datetime import datetime, timedelta, timezone

from src.domain.entities.manifest import InventoryItem, Manifest
from src.domain.services.change_detection import diff_inventory

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _item(oid, name, modify=T0, schema="dbo", type_="procedure", hash_=None):
    return InventoryItem(schema=schema, name=name, type=type_, object_id=oid, modify_date=modify, hash=hash_)


def _manifest(items):
    return Manifest(server="demo", database="DemoDB", objects=items)


def test_added_and_unchanged():
    prev = _manifest([_item(1, "SP_A", hash_="sha256:x")])
    cur = [_item(1, "SP_A"), _item(2, "SP_B")]
    diff = diff_inventory(prev, cur, discovery_complete=True)
    assert [i.object_id for i in diff.added] == [2]
    assert [i.object_id for i in diff.unchanged] == [1]


def test_modify_date_change_becomes_candidate():
    prev = _manifest([_item(1, "SP_A", hash_="sha256:x")])
    cur = [_item(1, "SP_A", modify=T0 + timedelta(days=1))]
    diff = diff_inventory(prev, cur, discovery_complete=True)
    assert [i.object_id for i in diff.candidates] == [1]


def test_rename_detected_by_object_id():
    prev = _manifest([_item(1, "SP_OLD", hash_="sha256:x")])
    cur = [_item(1, "SP_NEW")]
    diff = diff_inventory(prev, cur, discovery_complete=True)
    assert len(diff.renamed) == 1
    old, new = diff.renamed[0]
    assert (old.name, new.name) == ("SP_OLD", "SP_NEW")


def test_soft_delete_safety_when_discovery_incomplete():
    prev = _manifest([_item(1, "SP_A"), _item(2, "SP_B")])
    cur = [_item(1, "SP_A")]
    incomplete = diff_inventory(prev, cur, discovery_complete=False)
    assert incomplete.removed == []  # eksik nesneler silinmez
    complete = diff_inventory(prev, cur, discovery_complete=True)
    assert [i.object_id for i in complete.removed] == [2]
