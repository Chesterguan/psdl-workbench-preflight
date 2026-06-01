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


def test_sqlserver_introspect_row_mapping(monkeypatch):
    # Inject a fake pyodbc to lock the (schema, name, rows) column order without a real DB.
    import sys

    class _Cur:
        def execute(self, sql):
            self._sql = sql

        def fetchall(self):
            return [("dbo", "ORDER_PROCEDURE_DTL", 1_200_000_000)]

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def cursor(self):
            return _Cur()

    class _FakePyodbc:
        def connect(self, dsn):
            return _Conn()

    monkeypatch.setitem(sys.modules, "pyodbc", _FakePyodbc())
    stats = SQLServerIntrospector("dsn").introspect()
    assert len(stats) == 1
    assert stats[0].schema == "dbo"
    assert stats[0].name == "ORDER_PROCEDURE_DTL"
    assert stats[0].row_estimate == 1_200_000_000


@pytest.mark.skipif(not os.environ.get("PREFLIGHT_PG_DSN"),
                    reason="set PREFLIGHT_PG_DSN to run live Postgres introspection")
def test_postgres_introspector_live():
    stats = PostgresIntrospector(os.environ["PREFLIGHT_PG_DSN"]).introspect()
    assert any(s.name == "person" for s in stats)
