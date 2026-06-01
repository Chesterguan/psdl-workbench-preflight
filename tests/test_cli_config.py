import os
import tempfile

from preflight.cli import main


def _write_query(text="SELECT person_id FROM measurement WHERE measurement_concept_id = 3016723"):
    fd, path = tempfile.mkstemp(suffix=".sql")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


def test_env_supplies_defaults(monkeypatch, capsys):
    monkeypatch.setenv("PREFLIGHT_CATALOG", "omop")
    monkeypatch.setenv("PREFLIGHT_DIALECT", "duckdb")
    path = _write_query()
    rc = main(["check", path])  # no --catalog/--dialect flags
    out = capsys.readouterr().out
    assert rc == 0
    assert "STUDY SUMMARY" in out.upper()
    os.unlink(path)


def test_explicit_flag_overrides_env(monkeypatch, capsys):
    monkeypatch.setenv("PREFLIGHT_CATALOG", "does_not_exist")
    path = _write_query()
    rc = main(["check", path, "--catalog", "omop", "--dialect", "duckdb"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STUDY SUMMARY" in out.upper()
    os.unlink(path)


def test_catalog_bootstrap_duckdb_writes_yaml(tmp_path, capsys):
    # Build a real DuckDB file, bootstrap a catalog from it into tmp, load it back.
    from fixtures.build_omop import build_omop_duckdb
    db = str(tmp_path / "omop.duckdb")
    con = build_omop_duckdb(db)
    con.close()
    out_yaml = str(tmp_path / "omop_live.yaml")
    rc = main(["catalog-bootstrap", "--duckdb-path", db, "--schema-name", "omop_live",
               "--heuristic", "omop", "--out", out_yaml])
    assert rc == 0
    assert os.path.exists(out_yaml)
    text = open(out_yaml).read()
    assert text.startswith("# AUTO-GENERATED")
    assert "measurement" in text
