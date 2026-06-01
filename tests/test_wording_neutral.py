from preflight.parse.sql import parse_sql
from preflight.catalog.loader import load_catalog
from preflight.contracts import ScaleEstimate
from preflight.risk import assess_risk
from preflight.optimize import recommend


def test_risk_reason_is_schema_neutral():
    parsed = parse_sql("SELECT * FROM measurement", dialect="duckdb")
    cat = load_catalog("omop")
    _, reasons = assess_risk(parsed, cat, ScaleEstimate(output_records=1_500_000_000))
    joined = " ".join(reasons).lower()
    assert "high-volume event table" in joined
    assert "clinical event table" not in joined


def test_optimization_action_is_schema_neutral():
    parsed = parse_sql("SELECT * FROM measurement", dialect="duckdb")
    cat = load_catalog("omop")
    recs = recommend(parsed, cat, ScaleEstimate(output_records=1_500_000_000))
    actions = " ".join(r.action.lower() for r in recs)
    assert "selective filter" in actions
    assert "concept filter" not in actions
