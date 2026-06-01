import yaml

from preflight.catalog.bootstrap import TableStat, bootstrap_catalog, to_yaml


STATS = [
    TableStat(name="ORDER_PROCEDURE_DTL", row_estimate=1_200_000_000),
    TableStat(name="PATIENT_ENCOUNTER_DTL", row_estimate=40_000_000),
    TableStat(name="ALL_PATIENTS", row_estimate=2_000_000),
    TableStat(name="OR_CASE_KEY_XREF", row_estimate=5_000_000),
]


def test_bootstrap_catalog_builds_expected_profiles():
    doc = bootstrap_catalog(STATS, schema="clarity", heuristic="epic",
                            default_dialect="tsql", stats_as_of="2026-06-01")
    assert doc["schema"] == "clarity"
    assert doc["default_dialect"] == "tsql"
    assert doc["stats_as_of"] == "2026-06-01"
    t = doc["tables"]
    assert t["ORDER_PROCEDURE_DTL"] == {
        "category": "clinical_event", "volume": "huge", "risk": "very_high",
        "row_estimate": 1_200_000_000,
    }
    assert t["PATIENT_ENCOUNTER_DTL"]["category"] == "encounter"
    assert t["ALL_PATIENTS"]["category"] == "demographics"
    assert t["OR_CASE_KEY_XREF"]["category"] == "bridge"


def test_to_yaml_has_header_and_roundtrips():
    doc = bootstrap_catalog(STATS, schema="clarity", heuristic="epic", default_dialect="tsql")
    text = to_yaml(doc)
    assert text.startswith("# AUTO-GENERATED")
    parsed = yaml.safe_load(text)
    assert parsed["tables"]["ORDER_PROCEDURE_DTL"]["risk"] == "very_high"
