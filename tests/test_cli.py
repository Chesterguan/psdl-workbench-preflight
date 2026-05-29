import os
import tempfile

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
