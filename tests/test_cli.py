import os
import tempfile

import pytest

from preflight.cli import main


def _write_query():
    fd, path = tempfile.mkstemp(suffix=".sql")
    with os.fdopen(fd, "w") as fh:
        fh.write("SELECT person_id FROM measurement WHERE measurement_concept_id = 3016723")
    return path


def test_cli_text_output(capsys):
    path = _write_query()
    rc = main(["check", path, "--dialect", "duckdb", "--catalog", "omop", "--target", "omop"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "STUDY SUMMARY" in out.upper()
    os.unlink(path)


def test_cli_json_output(capsys):
    path = _write_query()
    rc = main(["check", path, "--dialect", "duckdb", "--catalog", "omop", "--format", "json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"risk_level"' in out
    os.unlink(path)


def test_cli_with_fixture_connector_runs_plan(capsys):
    path = _write_query()
    rc = main(["check", path, "--dialect", "duckdb", "--catalog", "omop",
               "--target", "omop", "--duckdb-fixture"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "QUERY PLAN" in out.upper()
    os.unlink(path)


def test_cli_with_duckdb_path_runs_plan(capsys, tmp_path):
    # A real, local DuckDB file (not the synthetic in-memory fixture).
    from fixtures.build_omop import build_omop_duckdb

    db_path = str(tmp_path / "omop.duckdb")
    con = build_omop_duckdb(db_path)
    con.close()  # release the write lock so the CLI can open it read-only

    path = _write_query()
    rc = main(["check", path, "--dialect", "duckdb", "--catalog", "omop",
               "--target", "omop", "--duckdb-path", db_path])
    out = capsys.readouterr().out
    assert rc == 0
    assert "QUERY PLAN" in out.upper()
    assert "SEQ_SCAN" in out.upper()  # live plan really ran EXPLAIN against the file
    os.unlink(path)


def test_cli_connector_flags_are_mutually_exclusive():
    path = _write_query()
    with pytest.raises(SystemExit):  # argparse rejects two connector sources
        main(["check", path, "--catalog", "omop",
              "--duckdb-fixture", "--postgres-dsn", "postgresql://x"])
    os.unlink(path)


@pytest.mark.skipif(not os.environ.get("PREFLIGHT_PG_DSN"),
                    reason="set PREFLIGHT_PG_DSN to run the live Postgres CLI test")
def test_cli_with_postgres_dsn_runs_plan(capsys):
    path = _write_query()
    rc = main(["check", path, "--dialect", "postgres", "--catalog", "omop",
               "--target", "omop", "--postgres-dsn", os.environ["PREFLIGHT_PG_DSN"]])
    out = capsys.readouterr().out
    assert rc == 0
    assert "QUERY PLAN" in out.upper()
    os.unlink(path)
