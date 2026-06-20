"""M4 saf domain servisleri — türkçe fold, kalite kapısı, card builder."""

from src.domain.entities.catalog import CatalogObject, Parameter, TableRef
from src.domain.services.card_builder import build_object_card
from src.domain.services.quality_gate import collect_identifiers, validate_summary
from src.domain.services.turkish_fold import turkish_fold


def test_turkish_fold_collapses_dotted_dotless_i():
    # SP_TEKLİF ile SP_TEKLIF aynı normalize forma düşmeli (design/07)
    assert turkish_fold("SP_TEKLİF") == turkish_fold("SP_TEKLIF")
    assert turkish_fold("SP_TEKLİF") == "sp_teklif"


def test_turkish_fold_preserves_special_chars():
    assert turkish_fold("ŞÇĞÖÜ") == "şçğöü"  # aksan ezilmez


def test_quality_gate_accepts_valid_identifiers():
    valid = collect_identifiers(tables=["dbo.TEKLIF"], columns=["Sure"], params=["@KullaniciID"])
    assert validate_summary("Kullanıcı için dbo.TEKLIF üzerinden Sure hesaplar (@KullaniciID).", valid)


def test_quality_gate_rejects_hallucinated_table():
    valid = collect_identifiers(tables=["dbo.TEKLIF"], columns=[], params=[])
    # UYDURMA_TABLO yapısal metadata'da yok → reddedilir
    assert not validate_summary("Bu nesne UYDURMA_TABLO tablosunu kullanır.", valid)


def _obj(summary=None):
    o = CatalogObject(
        uid="demo/DemoDB/10", alias="demo/DemoDB/dbo/SP_X", server="demo", database="DemoDB",
        schema="dbo", name="SP_X", type="procedure", object_id=10,
    )
    o.parameters = [Parameter(name="@KullaniciID", type="INT")]
    o.reads_tables = [TableRef(name="dbo.TEKLIF")]
    o.summary = summary
    o.category = "teklif"
    return o


def test_card_with_summary():
    card = build_object_card(_obj(summary="Teklif sürelerini hesaplar"))
    assert "[procedure] dbo.SP_X" in card
    assert "Özet: Teklif sürelerini hesaplar" in card
    assert "Kategori: teklif" in card
    assert "dbo.TEKLIF" in card


def test_card_structural_only_when_no_summary():
    card = build_object_card(_obj(summary=None))
    assert "Özet:" not in card  # yapısal-only fallback (design/07)
    assert "[procedure] dbo.SP_X" in card
