from preflight.parse.sql import parse_sql
from preflight.catalog.loader import load_catalog
from preflight.contracts import ScaleEstimate
from preflight.optimize import recommend


def test_unfiltered_event_table_recommends_filter():
    parsed = parse_sql("SELECT * FROM measurement", dialect="duckdb")
    cat = load_catalog("omop")
    recs = recommend(parsed, cat, ScaleEstimate(output_records=1_500_000_000))
    actions = " ".join(r.action.lower() for r in recs)
    assert "filter" in actions or "concept" in actions
    assert all(r.action for r in recs)


def test_small_clean_query_has_no_recommendations():
    parsed = parse_sql("SELECT person_id FROM person WHERE person_id = 5", dialect="duckdb")
    cat = load_catalog("omop")
    recs = recommend(parsed, cat, ScaleEstimate(output_records=1))
    assert recs == []
