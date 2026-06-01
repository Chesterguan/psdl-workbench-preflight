import pytest

from preflight.parse.sql import parse_sql, PreflightParseError

SQL = """
WITH baseline AS (
  SELECT person_id FROM measurement WHERE measurement_concept_id = 3016723
)
SELECT p.person_id, COUNT(m.value_as_number) AS n
FROM person p
JOIN measurement m ON p.person_id = m.person_id
JOIN baseline b ON b.person_id = p.person_id
WHERE m.measurement_date BETWEEN :s AND :e
GROUP BY p.person_id
"""


def test_base_tables_exclude_ctes():
    p = parse_sql(SQL, dialect="duckdb")
    assert p.base_tables == ["measurement", "person"]   # sorted, no 'baseline'
    assert p.cte_names == ["baseline"]


def test_join_and_aggregation_detection():
    p = parse_sql(SQL, dialect="duckdb")
    assert p.join_count == 2
    assert p.has_aggregation is True
    # equality/filter columns referenced in WHERE/ON
    assert "measurement_concept_id" in p.filter_columns


def test_simple_query_no_joins():
    p = parse_sql("SELECT * FROM person", dialect="duckdb")
    assert p.base_tables == ["person"]
    assert p.join_count == 0
    assert p.has_aggregation is False


def test_invalid_sql_raises_typed_error():
    with pytest.raises(PreflightParseError):
        parse_sql("SELECT FROM WHERE )(", dialect="duckdb")
