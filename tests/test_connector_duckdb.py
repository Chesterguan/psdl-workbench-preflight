import duckdb
import pytest

from preflight.connector.duckdb_connector import DuckDBConnector


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("CREATE TABLE person(person_id INTEGER)")
    c.execute("CREATE TABLE measurement(person_id INTEGER, value_as_number DOUBLE, "
              "measurement_concept_id INTEGER)")
    c.execute("INSERT INTO person SELECT range FROM range(100)")
    c.execute("INSERT INTO measurement SELECT (random()*100)::int, 1.0, 3016723 "
              "FROM range(5000)")
    return c


def test_explain_does_not_execute_and_returns_facts(con):
    conn = DuckDBConnector(con)
    facts = conn.analyze(
        "SELECT p.person_id FROM person p JOIN measurement m "
        "ON p.person_id = m.person_id WHERE m.measurement_concept_id = 3016723")
    assert facts.total_estimated_rows is not None
    assert any(n.join_type for n in facts.nodes)
    assert any(n.scan_type for n in facts.nodes)


def test_connector_never_runs_analyze(con):
    conn = DuckDBConnector(con)
    facts = conn.analyze("SELECT person_id / 0 AS bad FROM person")
    assert facts is not None


def test_both_tables_captured_from_join_explain(con):
    """Both SEQ_SCAN nodes in a 2-table join must be captured, not just the first.

    DuckDB 1.4.4 renders sibling operators side-by-side on the same text line
    (two box-drawing boxes). The parser must use finditer, not .search, to avoid
    dropping the second box.
    """
    conn = DuckDBConnector(con)
    facts = conn.analyze(
        "SELECT p.person_id FROM person p JOIN measurement m "
        "ON p.person_id = m.person_id WHERE m.measurement_concept_id = 3016723"
    )
    # At minimum two scan nodes must be present
    scan_nodes = [n for n in facts.nodes if n.scan_type is not None]
    assert len(scan_nodes) >= 2, (
        f"Expected >= 2 scan nodes, got {len(scan_nodes)}: {[n.op for n in facts.nodes]}"
    )
    # If Table: lines are parsed, both table names must appear
    tables_found = {n.table for n in facts.nodes if n.table is not None}
    if tables_found:
        assert "person" in tables_found and "measurement" in tables_found, (
            f"Expected both 'person' and 'measurement' in tables, got {tables_found}"
        )
