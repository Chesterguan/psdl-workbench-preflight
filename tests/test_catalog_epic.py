from preflight.catalog.loader import load_catalog


def test_clarity_catalog_categories_and_dialect():
    cat = load_catalog("clarity")
    assert cat.default_dialect == "tsql"
    assert cat.profile("OR_ENCOUNTER_DTL").category == "encounter"
    assert cat.profile("ORDER_PROCEDURE_DTL").category == "clinical_event"
    assert cat.profile("ORDER_PROCEDURE_DTL").risk == "very_high"
    assert cat.profile("ALL_PATIENTS").category == "demographics"
    assert cat.profile("OR_CASE_KEY_XREF").category == "bridge"
    assert cat.selectivity("ORDER_PROCEDURE_DTL.PROC_CD") is not None


def test_caboodle_catalog_has_facts_and_dims():
    cat = load_catalog("caboodle")
    assert cat.default_dialect == "tsql"
    assert cat.profile("EncounterFact").category == "encounter"
    assert cat.profile("DiagnosisEventFact").category == "clinical_event"
    assert cat.profile("PatientDim").category == "demographics"
