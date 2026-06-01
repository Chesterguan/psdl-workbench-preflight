from preflight.parse.sql import parse_sql
from preflight.catalog.loader import load_catalog
from preflight.bottleneck import find_bottlenecks

SQL = """
SELECT p.person_id, m.value_as_number
FROM person p
JOIN measurement m ON p.person_id = m.person_id
JOIN visit_occurrence v ON v.person_id = p.person_id
"""


def test_largest_table_is_primary_bottleneck():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    bns = find_bottlenecks(parsed, cat)
    assert bns[0].component == "measurement"
    assert bns[0].contribution_pct >= bns[1].contribution_pct
    assert bns[0].contribution_pct > 50


def test_contributions_sum_to_about_100():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    bns = find_bottlenecks(parsed, cat)
    total = sum(b.contribution_pct for b in bns)
    assert 98 <= total <= 102
