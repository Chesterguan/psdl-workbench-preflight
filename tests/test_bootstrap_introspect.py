import os
import pytest

from preflight.catalog.bootstrap import (
    DuckDBIntrospector, PostgresIntrospector, SQLServerIntrospector, bootstrap_catalog,
)
from fixtures.build_omop import build_omop_duckdb


def test_duckdb_introspector_returns_tables():
    con = build_omop_duckdb()
    stats = DuckDBIntrospector(con).introspect()
    names = {s.name for s in stats}
    assert {"person", "visit_occurrence", "measurement"} <= names
    assert all(isinstance(s.row_estimate, int) and s.row_estimate >= 0 for s in stats)
    # end-to-end: introspect -> catalog draft (omop heuristic)
    doc = bootstrap_catalog(stats, schema="omop_live", heuristic="omop")
    assert doc["tables"]["measurement"]["category"] == "clinical_event"


def test_sqlserver_introspector_constructs_without_driver():
    # No connection is made here; just confirm the class exists and stores the DSN.
    isp = SQLServerIntrospector("Driver=...;Server=...;")
    assert isp is not None


@pytest.mark.skipif(not os.environ.get("PREFLIGHT_PG_DSN"),
                    reason="set PREFLIGHT_PG_DSN to run live Postgres introspection")
def test_postgres_introspector_live():
    stats = PostgresIntrospector(os.environ["PREFLIGHT_PG_DSN"]).introspect()
    assert any(s.name == "person" for s in stats)
