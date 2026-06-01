from preflight.parse.sql import parse_sql
from preflight.catalog.loader import load_catalog
from preflight.contracts import RiskLevel, ScaleEstimate
from preflight.risk import assess_risk


def _scale(output):
    return ScaleEstimate(output_records=output)


def test_high_risk_table_and_large_output():
    parsed = parse_sql(
        "SELECT * FROM measurement WHERE measurement_concept_id = 3016723", dialect="duckdb")
    cat = load_catalog("omop")
    level, reasons = assess_risk(parsed, cat, _scale(2_000_000))
    assert level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert any("measurement" in r.lower() for r in reasons)


def test_low_risk_small_demographics_query():
    parsed = parse_sql("SELECT person_id FROM person WHERE person_id = 5", dialect="duckdb")
    cat = load_catalog("omop")
    level, reasons = assess_risk(parsed, cat, _scale(1))
    assert level == RiskLevel.LOW


def test_unfiltered_clinical_event_table_is_critical():
    parsed = parse_sql("SELECT * FROM measurement", dialect="duckdb")
    cat = load_catalog("omop")
    level, reasons = assess_risk(parsed, cat, _scale(1_500_000_000))
    assert level == RiskLevel.CRITICAL
    assert any("no filter" in r.lower() or "unfiltered" in r.lower() for r in reasons)
