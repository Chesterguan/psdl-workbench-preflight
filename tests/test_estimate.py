from preflight.parse.sql import parse_sql
from preflight.catalog.loader import load_catalog
from preflight.contracts import Confidence, RuntimeCategory
from preflight.estimate import estimate_scale

SQL = """
SELECT p.person_id, m.value_as_number
FROM person p
JOIN measurement m ON p.person_id = m.person_id
WHERE m.measurement_concept_id = 3016723
"""


def test_estimate_uses_largest_table_as_driver():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    scale, runtime = estimate_scale(parsed, cat, catalog_known_ratio=1.0)
    assert scale.events == 1_500_000_000
    assert 0 < scale.output_records < 1_500_000_000
    assert scale.confidence == Confidence.MEDIUM
    assert runtime in set(RuntimeCategory)


def test_plan_rows_override_takes_precedence():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    scale, _ = estimate_scale(parsed, cat, catalog_known_ratio=1.0, plan_rows=42)
    assert scale.output_records == 42
    assert scale.confidence == Confidence.HIGH


def test_runtime_category_thresholds():
    from preflight.estimate import runtime_for_rows
    assert runtime_for_rows(5_000) == RuntimeCategory.FAST
    assert runtime_for_rows(500_000) == RuntimeCategory.MODERATE
    assert runtime_for_rows(20_000_000) == RuntimeCategory.HEAVY
    assert runtime_for_rows(5_000_000_000) == RuntimeCategory.EXTREME
