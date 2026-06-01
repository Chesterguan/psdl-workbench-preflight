import os
import tempfile
import pytest

from preflight.cli import main


def _write_query(text="SELECT 1 AS x"):
    fd, path = tempfile.mkstemp(suffix=".sql")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


def test_sqlserver_dsn_mutually_exclusive_with_duckdb_fixture():
    path = _write_query()
    with pytest.raises(SystemExit):  # argparse rejects two connector sources
        main(["check", path, "--catalog", "omop", "--dialect", "tsql",
              "--duckdb-fixture", "--sqlserver-dsn", "Driver=x;"])
    os.unlink(path)


def test_build_connector_resolves_sqlserver_dsn():
    # _build_connector should return a SQLServerConnector when --sqlserver-dsn is given,
    # without importing pyodbc (the driver import is deferred to .analyze()).
    from types import SimpleNamespace
    from preflight.cli import _build_connector
    from preflight.connector.sqlserver_connector import SQLServerConnector
    args = SimpleNamespace(duckdb_fixture=False, duckdb_path=None,
                           postgres_dsn=None, sqlserver_dsn="Driver=x;Server=y;")
    conn = _build_connector(args)
    assert isinstance(conn, SQLServerConnector)
