import os

from preflight.catalog.loader import load_catalog


def test_user_catalog_dir_takes_precedence_over_packaged(tmp_path, monkeypatch):
    # A user catalog dir with a custom 'omop.yaml' overrides the packaged one.
    (tmp_path / "omop.yaml").write_text(
        "schema: omop\ndefault_dialect: duckdb\ntables:\n  zzz_user_marker:\n    volume: tiny\n")
    monkeypatch.setenv("PREFLIGHT_CATALOG_DIR", str(tmp_path))
    cat = load_catalog("omop")
    assert cat.is_known("zzz_user_marker")
    assert cat.default_dialect == "duckdb"


def test_falls_back_to_packaged_when_not_in_user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("PREFLIGHT_CATALOG_DIR", str(tmp_path))  # empty dir
    cat = load_catalog("omop")  # packaged omop.yaml
    assert cat.is_known("measurement")


def test_default_dialect_defaults_to_generic_when_absent():
    cat = load_catalog("omop")  # packaged omop.yaml has no default_dialect
    assert cat.default_dialect == "generic"


def test_stats_as_of_is_exposed_when_present(tmp_path, monkeypatch):
    (tmp_path / "c.yaml").write_text(
        "schema: c\nstats_as_of: '2026-06-01'\ntables:\n  t:\n    volume: tiny\n")
    monkeypatch.setenv("PREFLIGHT_CATALOG_DIR", str(tmp_path))
    assert load_catalog("c").stats_as_of == "2026-06-01"
    assert load_catalog("omop").stats_as_of is None  # absent in packaged seed
