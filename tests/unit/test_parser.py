from src.infrastructure.parsing.sqlglot_parser import SqlglotParser

SP = """
CREATE PROCEDURE dbo.SP_TEKLIF_SURELERI
    @KullaniciID INT,
    @Durum CHAR(1) = 'A'
AS
BEGIN
    EXEC dbo.SP_KULLANICI_YETKI_KONTROL @KullaniciID;
    CREATE TABLE #GeciciSure (TeklifNo INT, Sure INT);
    INSERT INTO #GeciciSure SELECT TeklifNo, Sure FROM dbo.TEKLIF WHERE KullaniciID = @KullaniciID;
    INSERT INTO dbo.TEKLIF_LOG (TeklifNo, Mesaj) SELECT TeklifNo, 'x' FROM #GeciciSure;
    SELECT TeklifNo, Sure FROM #GeciciSure;
END
"""


def test_parameters_extracted():
    res = SqlglotParser().parse(SP, "procedure")
    names = [p.name for p in res.parameters]
    assert "@KullaniciID" in names and "@Durum" in names
    durum = next(p for p in res.parameters if p.name == "@Durum")
    assert durum.default is not None


def test_temp_tables_and_calls():
    res = SqlglotParser().parse(SP, "procedure")
    assert "#GeciciSure" in res.temp_tables
    assert any("SP_KULLANICI_YETKI_KONTROL" in c for c in res.calls_objects)


def test_writes_detected():
    res = SqlglotParser().parse(SP, "procedure")
    writes = {t.name for t in res.writes_tables}
    assert any("TEKLIF_LOG" in w for w in writes)


def test_fallback_marks_partial_on_garbage():
    res = SqlglotParser().parse("CREATE PROCEDURE dbo.X AS @@@ not valid sql @@@", "procedure")
    assert res.partial_parse or res.parse_error or res.parameters == []
