from preflight.catalog.bootstrap import category_for, volume_for, risk_for


def test_epic_category_heuristic():
    assert category_for("OR_ENCOUNTER_DTL", "epic") == "encounter"
    assert category_for("ORDER_PROCEDURE_DTL", "epic") == "clinical_event"
    assert category_for("ALL_PATIENTS", "epic") == "demographics"
    assert category_for("ALL_PATIENT_SNAPSHOTS", "epic") == "dimension"
    assert category_for("ALL_PATIENT_IDENTITIES", "epic") == "dimension"
    assert category_for("ALL_PROVIDERS", "epic") == "dimension"
    assert category_for("OR_CASE_KEY_XREF", "epic") == "bridge"
    assert category_for("EncounterFact", "epic") == "encounter"
    assert category_for("DiagnosisEventFact", "epic") == "clinical_event"
    assert category_for("PatientDim", "epic") == "demographics"
    assert category_for("DepartmentDim", "epic") == "dimension"


def test_omop_category_heuristic():
    assert category_for("measurement", "omop") == "clinical_event"
    assert category_for("person", "omop") == "demographics"
    assert category_for("visit_occurrence", "omop") == "encounter"
    assert category_for("totally_unknown", "omop") == "unknown"


def test_volume_tiers():
    assert volume_for(500) == "tiny"
    assert volume_for(50_000) == "small"
    assert volume_for(1_000_000) == "medium"
    assert volume_for(40_000_000) == "large"
    assert volume_for(2_000_000_000) == "huge"


def test_risk_from_category_and_volume():
    assert risk_for("clinical_event", "huge") == "very_high"
    assert risk_for("clinical_event", "large") == "high"
    assert risk_for("encounter", "huge") == "high"   # encounters less risky than raw events
    assert risk_for("dimension", "huge") == "low"
    assert risk_for("bridge", "large") == "low"
