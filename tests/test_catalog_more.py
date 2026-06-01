from preflight.catalog.loader import load_catalog


def test_epic_catalog_has_flowsheet_table():
    cat = load_catalog("epic")
    prof = cat.profile("IP_FLWSHT_MEAS")
    assert prof.volume == "huge"
    assert prof.risk == "very_high"


def test_pcornet_catalog_has_lab_result_cm():
    cat = load_catalog("pcornet")
    assert cat.is_known("LAB_RESULT_CM")
