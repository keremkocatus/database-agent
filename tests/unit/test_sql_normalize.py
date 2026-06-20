from src.domain.services.sql_normalize import content_hash, normalize


def test_normalize_crlf_and_trailing():
    assert normalize("SELECT 1   \r\nFROM t  \r\n") == "SELECT 1\nFROM t"


def test_normalize_preserves_comments_and_literals():
    sql = "-- yorum\nSELECT '  bosluk  ' AS x"
    assert "-- yorum" in normalize(sql)
    assert "'  bosluk  '" in normalize(sql)


def test_hash_stable_across_line_endings():
    a = content_hash("SELECT 1\nFROM t")
    b = content_hash("SELECT 1\r\nFROM t  ")
    assert a == b
    assert a.startswith("sha256:")


def test_hash_changes_on_content_change():
    assert content_hash("SELECT 1") != content_hash("SELECT 2")
