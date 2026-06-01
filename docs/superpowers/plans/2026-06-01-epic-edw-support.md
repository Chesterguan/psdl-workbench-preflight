# Epic EDW Support (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Epic EDW (T-SQL) support to Preflight via a read-only catalog **bootstrapper**, seed Clarity/Caboodle catalogs, a `.env`/fixed-location config layer, and T-SQL dialect defaulting — CLI-first, fully tested, no UI.

**Architecture:** A new `preflight/catalog/bootstrap.py` introspects a DB's *system catalogs* (read-only, counts/stats only — no PHI, no user-query execution) and emits catalog-YAML drafts via pure mapping functions. `load_catalog` gains directory resolution (`$PREFLIGHT_CATALOG_DIR` → packaged) and a per-catalog `default_dialect`. A tiny `.env` reader feeds CLI defaults. The analysis engine is unchanged except a schema-neutral wording tweak (categories reuse the existing `encounter`/`clinical_event`/`demographics` vocabulary, so no rule changes).

**Tech Stack:** Python 3.9, sqlglot (tsql dialect), pyyaml, duckdb, psycopg (optional), pyodbc (optional, SQL Server), pytest. Spec: `docs/superpowers/specs/2026-06-01-epic-edw-support-design.md`.

**Conventions:** Work from `/Users/ziyuanguan/psdl-workbench-preflight`. Activate the venv in every shell step: `. .venv/bin/activate`. Stage only the files named in each commit (never `git add -A`). End every commit message with:
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

### Task 1: Bootstrapper mapping functions (pure, no I/O)

**Files:**
- Create: `preflight/catalog/bootstrap.py`
- Test: `tests/test_bootstrap_mapping.py`

- [ ] **Step 1: Write the failing test**

`tests/test_bootstrap_mapping.py`:
```python
from preflight.catalog.bootstrap import category_for, volume_for, risk_for


def test_epic_category_heuristic():
    assert category_for("OR_ENCOUNTER_DTL", "epic") == "encounter"
    assert category_for("ORDER_PROCEDURE_DTL", "epic") == "clinical_event"
    assert category_for("ALL_PATIENTS", "epic") == "demographics"
    assert category_for("ALL_PATIENT_SNAPSHOTS", "epic") == "dimension"
    assert category_for("ALL_PATIENT_IDENTITIES", "epic") == "dimension"
    assert category_for("ALL_PROVIDERS", "epic") == "dimension"
    assert category_for("OR_CASE_KEY_XREF", "epic") == "bridge"
    assert category_for("EncounterFact", "epic") == "encounter"
    assert category_for("DiagnosisEventFact", "epic") == "clinical_event"
    assert category_for("PatientDim", "epic") == "demographics"
    assert category_for("DepartmentDim", "epic") == "dimension"


def test_omop_category_heuristic():
    assert category_for("measurement", "omop") == "clinical_event"
    assert category_for("person", "omop") == "demographics"
    assert category_for("visit_occurrence", "omop") == "encounter"
    assert category_for("totally_unknown", "omop") == "unknown"


def test_volume_tiers():
    assert volume_for(500) == "tiny"
    assert volume_for(50_000) == "small"
    assert volume_for(1_000_000) == "medium"
    assert volume_for(40_000_000) == "large"
    assert volume_for(2_000_000_000) == "huge"


def test_risk_from_category_and_volume():
    assert risk_for("clinical_event", "huge") == "very_high"
    assert risk_for("clinical_event", "large") == "high"
    assert risk_for("encounter", "huge") == "high"   # encounters less risky than raw events
    assert risk_for("dimension", "huge") == "low"
    assert risk_for("bridge", "large") == "low"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `. .venv/bin/activate && python -m pytest tests/test_bootstrap_mapping.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.catalog.bootstrap'`

- [ ] **Step 3: Write the implementation**

`preflight/catalog/bootstrap.py`:
```python
"""Read-only catalog bootstrapper: introspect system catalogs -> catalog YAML draft.

No user-query execution and no row VALUES are read — only table names, row-count
estimates, and (optionally) column n_distinct stats. Privacy-safe (see the privacy audit).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol

import yaml

# row_estimate -> volume tier (inverse of loader._VOLUME_ROWS thresholds)
_VOLUME_TIERS = [
    (1_000, "tiny"),
    (100_000, "small"),
    (2_000_000, "medium"),
    (50_000_000, "large"),
    (1_000_000_000, "huge"),
]

_RISK_BY_VOLUME = {
    "tiny": "low", "small": "low", "medium": "medium", "large": "high", "huge": "very_high",
}

_OMOP_CATEGORY = {
    "person": "demographics",
    "visit_occurrence": "encounter",
    "visit_detail": "encounter",
    "measurement": "clinical_event",
    "observation": "clinical_event",
    "drug_exposure": "clinical_event",
    "condition_occurrence": "clinical_event",
    "procedure_occurrence": "clinical_event",
    "device_exposure": "clinical_event",
    "death": "clinical_event",
}


@dataclass
class TableStat:
    """One row of system-catalog introspection: a table and its estimated row count."""
    name: str
    row_estimate: int
    schema: str = ""


def volume_for(row_estimate: int) -> str:
    for threshold, tier in _VOLUME_TIERS:
        if row_estimate < threshold:
            return tier
    return "huge"


def category_for(name: str, heuristic: str = "generic") -> str:
    n = name.upper()
    if heuristic == "epic":
        if n.endswith("_KEY_XREF"):
            return "bridge"
        if n.startswith("ALL_"):
            if "PATIENT" in n and "IDENT" not in n and "SNAPSHOT" not in n:
                return "demographics"
            return "dimension"
        if n.endswith("_DTL"):
            return "encounter" if "ENCOUNTER" in n else "clinical_event"
        if n.endswith("FACT"):
            return "encounter" if ("ENCOUNTER" in n or "CASE" in n) else "clinical_event"
        if n.endswith("DIM"):
            return "demographics" if "PATIENT" in n else "dimension"
        return "unknown"
    if heuristic == "omop":
        return _OMOP_CATEGORY.get(name.lower(), "unknown")
    return "unknown"


def risk_for(category: str, volume: str) -> str:
    if category not in ("clinical_event", "encounter"):
        return "low"
    tier = _RISK_BY_VOLUME.get(volume, "low")
    if category == "encounter" and tier == "very_high":
        tier = "high"  # encounter-grain tables are large but less risky than raw events
    return tier
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && python -m pytest tests/test_bootstrap_mapping.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/catalog/bootstrap.py tests/test_bootstrap_mapping.py
git commit -m "feat(preflight): bootstrapper mapping functions (category/volume/risk)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `bootstrap_catalog` + `to_yaml` (rowset → catalog dict → YAML)

**Files:**
- Modify: `preflight/catalog/bootstrap.py`
- Test: `tests/test_bootstrap_build.py`

- [ ] **Step 1: Write the failing test**

`tests/test_bootstrap_build.py`:
```python
import yaml

from preflight.catalog.bootstrap import TableStat, bootstrap_catalog, to_yaml


STATS = [
    TableStat(name="ORDER_PROCEDURE_DTL", row_estimate=1_200_000_000),
    TableStat(name="PATIENT_ENCOUNTER_DTL", row_estimate=40_000_000),
    TableStat(name="ALL_PATIENTS", row_estimate=2_000_000),
    TableStat(name="OR_CASE_KEY_XREF", row_estimate=5_000_000),
]


def test_bootstrap_catalog_builds_expected_profiles():
    doc = bootstrap_catalog(STATS, schema="clarity", heuristic="epic",
                            default_dialect="tsql", stats_as_of="2026-06-01")
    assert doc["schema"] == "clarity"
    assert doc["default_dialect"] == "tsql"
    assert doc["stats_as_of"] == "2026-06-01"
    t = doc["tables"]
    assert t["ORDER_PROCEDURE_DTL"] == {
        "category": "clinical_event", "volume": "huge", "risk": "very_high",
        "row_estimate": 1_200_000_000,
    }
    assert t["PATIENT_ENCOUNTER_DTL"]["category"] == "encounter"
    assert t["ALL_PATIENTS"]["category"] == "demographics"
    assert t["OR_CASE_KEY_XREF"]["category"] == "bridge"


def test_to_yaml_has_header_and_roundtrips():
    doc = bootstrap_catalog(STATS, schema="clarity", heuristic="epic", default_dialect="tsql")
    text = to_yaml(doc)
    assert text.startswith("# AUTO-GENERATED")
    parsed = yaml.safe_load(text)
    assert parsed["tables"]["ORDER_PROCEDURE_DTL"]["risk"] == "very_high"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `. .venv/bin/activate && python -m pytest tests/test_bootstrap_build.py -q`
Expected: FAIL — `ImportError: cannot import name 'bootstrap_catalog'`

- [ ] **Step 3: Write the implementation**

Append to `preflight/catalog/bootstrap.py`:
```python
def bootstrap_catalog(stats: List[TableStat], schema: str, heuristic: str = "generic",
                      default_dialect: Optional[str] = None,
                      stats_as_of: Optional[str] = None) -> dict:
    """Turn introspected TableStats into a catalog dict (omop.yaml shape)."""
    tables = {}
    for st in stats:
        cat = category_for(st.name, heuristic)
        vol = volume_for(st.row_estimate)
        tables[st.name] = {
            "category": cat,
            "volume": vol,
            "risk": risk_for(cat, vol),
            "row_estimate": int(st.row_estimate),
        }
    doc: dict = {"schema": schema, "tables": tables}
    if default_dialect:
        doc["default_dialect"] = default_dialect
    if stats_as_of:
        doc["stats_as_of"] = stats_as_of
    return doc


def to_yaml(doc: dict) -> str:
    """Serialize a catalog dict to YAML with a review-warning header."""
    header = "# AUTO-GENERATED draft — review category/risk before committing.\n"
    return header + yaml.safe_dump(doc, sort_keys=True, default_flow_style=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && python -m pytest tests/test_bootstrap_build.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/catalog/bootstrap.py tests/test_bootstrap_build.py
git commit -m "feat(preflight): bootstrap_catalog + to_yaml (rowset -> catalog draft)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Introspector backends (DuckDB real, Postgres gated, SQL Server)

**Files:**
- Modify: `preflight/catalog/bootstrap.py`
- Test: `tests/test_bootstrap_introspect.py`

- [ ] **Step 1: Write the failing test**

`tests/test_bootstrap_introspect.py`:
```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `. .venv/bin/activate && python -m pytest tests/test_bootstrap_introspect.py -q`
Expected: FAIL — `ImportError: cannot import name 'DuckDBIntrospector'`

- [ ] **Step 3: Write the implementation**

Append to `preflight/catalog/bootstrap.py`:
```python
class Introspector(Protocol):
    def introspect(self) -> List[TableStat]:
        """Return read-only table row-count estimates from system catalogs."""
        ...


class DuckDBIntrospector:
    def __init__(self, connection):
        self._con = connection

    def introspect(self) -> List[TableStat]:
        rows = self._con.execute(
            "SELECT table_name, estimated_size FROM duckdb_tables()"
        ).fetchall()
        return [TableStat(name=r[0], row_estimate=int(r[1] or 0)) for r in rows]


class PostgresIntrospector:
    _SQL = (
        "SELECT n.nspname, c.relname, c.reltuples::bigint "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind = 'r' "
        "AND n.nspname NOT IN ('pg_catalog', 'information_schema')"
    )

    def __init__(self, dsn: str):
        self._dsn = dsn

    def introspect(self) -> List[TableStat]:
        import psycopg
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(self._SQL)
                return [TableStat(name=r[1], row_estimate=max(int(r[2]), 0), schema=r[0])
                        for r in cur.fetchall()]


class SQLServerIntrospector:
    """Epic EDW path. Reads sys.partitions row counts (no scan, no data). Live run
    requires pyodbc/pymssql and a SQL Server connection — opt-in, not exercised in CI."""
    _SQL = (
        "SELECT s.name, t.name, SUM(p.rows) "
        "FROM sys.tables t "
        "JOIN sys.schemas s ON t.schema_id = s.schema_id "
        "JOIN sys.partitions p ON t.object_id = p.object_id AND p.index_id IN (0, 1) "
        "GROUP BY s.name, t.name"
    )

    def __init__(self, dsn: str):
        self._dsn = dsn

    def introspect(self) -> List[TableStat]:
        import pyodbc  # optional dependency, imported lazily
        with pyodbc.connect(self._dsn) as conn:
            cur = conn.cursor()
            cur.execute(self._SQL)
            return [TableStat(name=r[1], row_estimate=int(r[2] or 0), schema=r[0])
                    for r in cur.fetchall()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && python -m pytest tests/test_bootstrap_introspect.py -q`
Expected: PASS (2 run, 1 skipped). If `duckdb_tables().estimated_size` is NULL/0 on this DuckDB build, the test still passes (it only asserts `>= 0` and that names are present).

- [ ] **Step 5: Commit**

```bash
git add preflight/catalog/bootstrap.py tests/test_bootstrap_introspect.py
git commit -m "feat(preflight): introspectors (DuckDB/Postgres/SQL Server, read-only)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Catalog directory resolution + `default_dialect` in the loader

**Files:**
- Modify: `preflight/catalog/loader.py`
- Test: `tests/test_catalog_resolution.py`

- [ ] **Step 1: Write the failing test**

`tests/test_catalog_resolution.py`:
```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `. .venv/bin/activate && python -m pytest tests/test_catalog_resolution.py -q`
Expected: FAIL — `AttributeError: 'Catalog' object has no attribute 'default_dialect'`

- [ ] **Step 3: Write the implementation**

In `preflight/catalog/loader.py`, change `Catalog.__init__` to accept and store `default_dialect`:
```python
    def __init__(self, schema: str, tables: Dict[str, TableProfile],
                 joins: Dict[str, str], columns: Dict[str, float],
                 default_dialect: str = "generic"):
        self.schema = schema
        self._tables = tables
        self._joins = joins
        self._columns = columns
        self.default_dialect = default_dialect
```

Add a search-path resolver above `load_catalog`:
```python
def _catalog_search_dirs(catalog_dir: Optional[str] = None):
    dirs = []
    if catalog_dir:
        dirs.append(os.path.expanduser(catalog_dir))
    env_dir = os.environ.get("PREFLIGHT_CATALOG_DIR")
    if env_dir:
        dirs.append(os.path.expanduser(env_dir))
    dirs.append(os.path.expanduser("~/.preflight/catalogs"))
    dirs.append(_SCHEMAS_DIR)  # packaged seeds (lowest priority)
    return dirs
```

Replace the body of `load_catalog` so it resolves across dirs and reads `default_dialect`:
```python
def load_catalog(schema: str, catalog_dir: Optional[str] = None) -> Catalog:
    path = None
    for d in _catalog_search_dirs(catalog_dir):
        candidate = os.path.join(d, f"{schema}.yaml")
        if os.path.exists(candidate):
            path = candidate
            break
    if path is None:
        raise FileNotFoundError(f"No catalog for schema '{schema}' in any catalog dir")
    with open(path, "r") as fh:
        data = yaml.safe_load(fh) or {}

    tables: Dict[str, TableProfile] = {}
    for name, attrs in (data.get("tables") or {}).items():
        attrs = attrs or {}
        tables[name.lower()] = TableProfile(
            name=name,
            category=attrs.get("category", "unknown"),
            volume=attrs.get("volume", "unknown"),
            risk=attrs.get("risk", "unknown"),
            row_estimate=attrs.get("row_estimate"),
        )

    joins = {k.lower(): v for k, v in (data.get("joins") or {}).items()}
    columns = {k.lower(): float(v) for k, v in (data.get("columns") or {}).items()}
    return Catalog(schema=data.get("schema", schema), tables=tables, joins=joins,
                   columns=columns, default_dialect=data.get("default_dialect", "generic"))
```

Confirm `Optional` is imported (the file already imports `from typing import Dict, Optional`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && python -m pytest tests/test_catalog_resolution.py tests/test_catalog.py tests/test_catalog_more.py -q`
Expected: PASS (resolution tests + existing catalog tests still green)

- [ ] **Step 5: Commit**

```bash
git add preflight/catalog/loader.py tests/test_catalog_resolution.py
git commit -m "feat(preflight): catalog dir resolution + per-catalog default_dialect

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Tiny `.env` loader (no new dependency)

**Files:**
- Create: `preflight/config.py`
- Test: `tests/test_config_env.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config_env.py`:
```python
import os

from preflight.config import load_dotenv


def test_load_dotenv_populates_environ_without_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        '# comment\n'
        'PREFLIGHT_DIALECT=tsql\n'
        'PREFLIGHT_CATALOG="clarity"\n'
        "PREFLIGHT_PG_DSN='postgresql://u@h/db'\n"
        "\n"
    )
    monkeypatch.delenv("PREFLIGHT_DIALECT", raising=False)
    monkeypatch.setenv("PREFLIGHT_CATALOG", "omop")  # pre-existing wins
    load_dotenv(str(env))
    assert os.environ["PREFLIGHT_DIALECT"] == "tsql"          # set from file
    assert os.environ["PREFLIGHT_CATALOG"] == "omop"          # not overridden
    assert os.environ["PREFLIGHT_PG_DSN"] == "postgresql://u@h/db"  # quotes stripped


def test_load_dotenv_missing_file_is_noop(tmp_path):
    load_dotenv(str(tmp_path / "nope.env"))  # must not raise
```

- [ ] **Step 2: Run it to verify it fails**

Run: `. .venv/bin/activate && python -m pytest tests/test_config_env.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.config'`

- [ ] **Step 3: Write the implementation**

`preflight/config.py`:
```python
"""Minimal .env support and config resolution. No external dependency."""
from __future__ import annotations

import os
from typing import List


def load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE lines from `path` into os.environ without overriding existing keys.
    Lines that are blank, start with '#', or lack '=' are ignored; surrounding single or
    double quotes are stripped from the value."""
    if not os.path.exists(path):
        return
    with open(path, "r") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, val)


def load_default_dotenvs() -> None:
    """Load .env from the current directory and ~/.preflight/.env (CWD wins)."""
    for path in (".env", os.path.expanduser("~/.preflight/.env")):
        load_dotenv(path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && python -m pytest tests/test_config_env.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/config.py tests/test_config_env.py
git commit -m "feat(preflight): tiny .env loader (no new dependency)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: CLI — `.env` auto-load, dialect/DSN/catalog defaults, `--catalog-dir`, `catalog-bootstrap` subcommand

**Files:**
- Modify: `preflight/cli.py`
- Test: `tests/test_cli_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_config.py`:
```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `. .venv/bin/activate && python -m pytest tests/test_cli_config.py -q`
Expected: FAIL — `--catalog`/`--dialect` are required-positional or the `catalog-bootstrap` subcommand doesn't exist (argparse error / SystemExit).

- [ ] **Step 3: Write the implementation**

Replace `preflight/cli.py` with:
```python
"""Command-line entry point for the Preflight analyzer."""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from preflight.catalog.loader import load_catalog
from preflight.config import load_default_dotenvs
from preflight.contracts import GeneratedSQL
from preflight.pipeline import run_preflight
from preflight.report.render import render_json, render_text


def _build_connector(args):
    """Resolve a connector from flags or PREFLIGHT_* env, or None. Read-only / EXPLAIN-only."""
    duckdb_path = getattr(args, "duckdb_path", None) or os.environ.get("PREFLIGHT_DUCKDB_PATH")
    pg_dsn = getattr(args, "postgres_dsn", None) or os.environ.get("PREFLIGHT_PG_DSN")
    if getattr(args, "duckdb_fixture", False):
        from fixtures.build_omop import build_omop_duckdb
        from preflight.connector.duckdb_connector import DuckDBConnector
        return DuckDBConnector(build_omop_duckdb())
    if duckdb_path:
        import duckdb
        from preflight.connector.duckdb_connector import DuckDBConnector
        return DuckDBConnector(duckdb.connect(duckdb_path, read_only=True))
    if pg_dsn:
        from preflight.connector.postgres_connector import PostgresConnector
        return PostgresConnector(pg_dsn)
    return None


def _cmd_check(args) -> int:
    with open(args.sql_file, "r") as fh:
        query = fh.read()
    catalog_name = args.catalog or os.environ.get("PREFLIGHT_CATALOG") or "omop"
    catalog = load_catalog(catalog_name, catalog_dir=args.catalog_dir)
    dialect = (args.dialect or os.environ.get("PREFLIGHT_DIALECT")
               or catalog.default_dialect or "generic")
    target = args.target or catalog_name
    sql = GeneratedSQL(query=query, dialect=dialect, target=target)
    report = run_preflight(sql, catalog, connector=_build_connector(args))
    print(render_json(report) if args.format == "json" else render_text(report))
    return 0


def _cmd_catalog_bootstrap(args) -> int:
    from preflight.catalog.bootstrap import (
        DuckDBIntrospector, PostgresIntrospector, SQLServerIntrospector,
        bootstrap_catalog, to_yaml,
    )
    if args.duckdb_path:
        import duckdb
        introspector = DuckDBIntrospector(duckdb.connect(args.duckdb_path, read_only=True))
        default_dialect = "duckdb"
    elif args.postgres_dsn or os.environ.get("PREFLIGHT_PG_DSN"):
        introspector = PostgresIntrospector(args.postgres_dsn or os.environ["PREFLIGHT_PG_DSN"])
        default_dialect = "postgres"
    elif args.sqlserver_dsn or os.environ.get("PREFLIGHT_SQLSERVER_DSN"):
        introspector = SQLServerIntrospector(
            args.sqlserver_dsn or os.environ["PREFLIGHT_SQLSERVER_DSN"])
        default_dialect = "tsql"
    else:
        print("error: provide --duckdb-path, --postgres-dsn, or --sqlserver-dsn",
              file=sys.stderr)
        return 2

    stats = introspector.introspect()
    doc = bootstrap_catalog(stats, schema=args.schema_name, heuristic=args.heuristic,
                            default_dialect=default_dialect, stats_as_of=args.stats_as_of)
    text = to_yaml(doc)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text)
        print(f"wrote {len(doc['tables'])} tables to {args.out}")
    else:
        print(text)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    load_default_dotenvs()
    parser = argparse.ArgumentParser(prog="preflight", description="SQL pre-execution analyzer")
    sub = parser.add_subparsers(dest="command", required=True)

    chk = sub.add_parser("check", help="Analyze a generated SQL file")
    chk.add_argument("sql_file", help="Path to a .sql file")
    chk.add_argument("--dialect", default=None)
    chk.add_argument("--catalog", default=None, help="Schema family catalog name")
    chk.add_argument("--catalog-dir", default=None, help="Directory to resolve catalogs from")
    chk.add_argument("--target", default=None, help="Execution-target label for the report")
    chk.add_argument("--format", choices=["text", "json"], default="text")
    conn_grp = chk.add_mutually_exclusive_group()
    conn_grp.add_argument("--duckdb-fixture", action="store_true",
                          help="Attach the synthetic OMOP DuckDB connector")
    conn_grp.add_argument("--duckdb-path", default=None,
                          help="Local DuckDB file (opened read-only)")
    conn_grp.add_argument("--postgres-dsn", default=None,
                          help="Postgres DSN for live EXPLAIN")
    chk.set_defaults(func=_cmd_check)

    bs = sub.add_parser("catalog-bootstrap",
                        help="Generate a catalog YAML from a DB's system catalogs (read-only)")
    bs.add_argument("--schema-name", required=True, help="Catalog/schema name to emit")
    bs.add_argument("--heuristic", choices=["epic", "omop", "generic"], default="generic")
    bs.add_argument("--out", default=None, help="Output YAML path (default: stdout)")
    bs.add_argument("--stats-as-of", default=None, help="Freshness label to stamp into the YAML")
    src = bs.add_mutually_exclusive_group()
    src.add_argument("--duckdb-path", default=None)
    src.add_argument("--postgres-dsn", default=None)
    src.add_argument("--sqlserver-dsn", default=None)
    bs.set_defaults(func=_cmd_catalog_bootstrap)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && python -m pytest tests/test_cli_config.py tests/test_cli.py -q`
Expected: PASS (new config tests + all existing CLI tests, since they pass `--dialect`/`--catalog` explicitly).

- [ ] **Step 5: Commit**

```bash
git add preflight/cli.py tests/test_cli_config.py
git commit -m "feat(preflight): CLI .env defaults, dialect/DSN resolution, catalog-bootstrap cmd

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Seed `clarity.yaml` + `caboodle.yaml` and catalog tests

**Files:**
- Create: `preflight/catalog/schemas/clarity.yaml`
- Create: `preflight/catalog/schemas/caboodle.yaml`
- Test: `tests/test_catalog_epic.py`

- [ ] **Step 1: Write the failing test**

`tests/test_catalog_epic.py`:
```python
from preflight.catalog.loader import load_catalog


def test_clarity_catalog_categories_and_dialect():
    cat = load_catalog("clarity")
    assert cat.default_dialect == "tsql"
    assert cat.profile("OR_ENCOUNTER_DTL").category == "encounter"
    assert cat.profile("ORDER_PROCEDURE_DTL").category == "clinical_event"
    assert cat.profile("ORDER_PROCEDURE_DTL").risk == "very_high"
    assert cat.profile("ALL_PATIENTS").category == "demographics"
    assert cat.profile("OR_CASE_KEY_XREF").category == "bridge"
    assert cat.selectivity("ORDER_PROCEDURE_DTL.PROC_CD") is not None


def test_caboodle_catalog_has_facts_and_dims():
    cat = load_catalog("caboodle")
    assert cat.default_dialect == "tsql"
    assert cat.profile("EncounterFact").category == "encounter"
    assert cat.profile("DiagnosisEventFact").category == "clinical_event"
    assert cat.profile("PatientDim").category == "demographics"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `. .venv/bin/activate && python -m pytest tests/test_catalog_epic.py -q`
Expected: FAIL — `FileNotFoundError: No catalog for schema 'clarity'`

- [ ] **Step 3: Write the seed catalogs**

`preflight/catalog/schemas/clarity.yaml`:
```yaml
# Seed Epic Clarity-derived EDW catalog (UF-style ALL_*/_DTL/_KEY_XREF naming).
# Row estimates are illustrative; regenerate with `preflight catalog-bootstrap`.
schema: clarity
default_dialect: tsql
tables:
  PATIENT_ENCOUNTER_DTL:
    category: encounter
    volume: large
    risk: high
    row_estimate: 40000000
  OR_ENCOUNTER_DTL:
    category: encounter
    volume: large
    risk: high
    row_estimate: 5000000
  AN_ENCOUNTER_DTL:
    category: encounter
    volume: medium
    risk: medium
    row_estimate: 3000000
  ORDER_PROCEDURE_DTL:
    category: clinical_event
    volume: huge
    risk: very_high
    row_estimate: 1200000000
  OR_CASE_PROCEDURE_DTL:
    category: clinical_event
    volume: large
    risk: high
    row_estimate: 30000000
  OR_LOG_PROCEDURE_DTL:
    category: clinical_event
    volume: large
    risk: high
    row_estimate: 25000000
  AN_BLOCK_DTL:
    category: clinical_event
    volume: medium
    risk: medium
    row_estimate: 2000000
  ALL_PATIENTS:
    category: demographics
    volume: medium
    risk: low
    row_estimate: 2000000
  ALL_PATIENT_SNAPSHOTS:
    category: dimension
    volume: large
    risk: low
    row_estimate: 12000000
  ALL_PATIENT_IDENTITIES:
    category: dimension
    volume: medium
    risk: low
    row_estimate: 3000000
  ALL_PROVIDERS:
    category: dimension
    volume: small
    risk: low
    row_estimate: 50000
  ALL_PROVIDER_IDENTITIES:
    category: dimension
    volume: small
    risk: low
    row_estimate: 80000
  ALL_OR_ROOMS:
    category: dimension
    volume: tiny
    risk: low
    row_estimate: 500
  ALL_OR_PROCEDURES:
    category: dimension
    volume: small
    risk: low
    row_estimate: 60000
  ALL_OR_ANESTHESIA_TYPES:
    category: dimension
    volume: tiny
    risk: low
    row_estimate: 200
  ALL_SERVICE_CODES:
    category: dimension
    volume: small
    risk: low
    row_estimate: 20000
  ALL_HOSPITAL_ORGANIZATIONS:
    category: dimension
    volume: small
    risk: low
    row_estimate: 5000
  OR_CASE_KEY_XREF:
    category: bridge
    volume: medium
    risk: low
    row_estimate: 5000000
  ORDR_PROC_KEY_XREF:
    category: bridge
    volume: large
    risk: low
    row_estimate: 60000000
  PATNT_ENCNTR_KEY_XREF:
    category: bridge
    volume: medium
    risk: low
    row_estimate: 4000000
joins:
  PATIENT_ENCOUNTER_DTL->ALL_PATIENTS: high
  PATIENT_ENCOUNTER_DTL->OR_ENCOUNTER_DTL: high
  OR_ENCOUNTER_DTL->ORDER_PROCEDURE_DTL: high
  OR_ENCOUNTER_DTL->OR_CASE_PROCEDURE_DTL: high
  PATIENT_ENCOUNTER_DTL->ORDER_PROCEDURE_DTL: high
columns:
  ORDER_PROCEDURE_DTL.PROC_CD: 0.001
  OR_ENCOUNTER_DTL.SCHD_DATE: 0.01
```

`preflight/catalog/schemas/caboodle.yaml`:
```yaml
# Seed Epic Caboodle (dimensional) catalog. Regenerate with `preflight catalog-bootstrap`.
schema: caboodle
default_dialect: tsql
tables:
  EncounterFact:
    category: encounter
    volume: huge
    risk: high
    row_estimate: 800000000
  SurgicalCaseFact:
    category: encounter
    volume: large
    risk: high
    row_estimate: 20000000
  DiagnosisEventFact:
    category: clinical_event
    volume: huge
    risk: very_high
    row_estimate: 3000000000
  LabComponentResultFact:
    category: clinical_event
    volume: huge
    risk: very_high
    row_estimate: 5000000000
  PatientDim:
    category: demographics
    volume: medium
    risk: low
    row_estimate: 2500000
  DepartmentDim:
    category: dimension
    volume: small
    risk: low
    row_estimate: 8000
  ProviderDim:
    category: dimension
    volume: small
    risk: low
    row_estimate: 60000
  DateDim:
    category: dimension
    volume: tiny
    risk: low
    row_estimate: 40000
joins:
  EncounterFact->PatientDim: high
  EncounterFact->DepartmentDim: high
  EncounterFact->DateDim: high
  DiagnosisEventFact->EncounterFact: high
  LabComponentResultFact->EncounterFact: high
columns:
  DiagnosisEventFact.DiagnosisKey: 0.0008
  LabComponentResultFact.LabComponentKey: 0.0005
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && python -m pytest tests/test_catalog_epic.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/catalog/schemas/clarity.yaml preflight/catalog/schemas/caboodle.yaml tests/test_catalog_epic.py
git commit -m "feat(preflight): seed Clarity + Caboodle catalogs (tsql default)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Schema-neutral wording in risk/optimize (review S1)

**Files:**
- Modify: `preflight/risk.py`
- Modify: `preflight/optimize.py`
- Test: `tests/test_wording_neutral.py`

- [ ] **Step 1: Write the failing test**

`tests/test_wording_neutral.py`:
```python
from preflight.parse.sql import parse_sql
from preflight.catalog.loader import load_catalog
from preflight.contracts import ScaleEstimate
from preflight.risk import assess_risk
from preflight.optimize import recommend


def test_risk_reason_is_schema_neutral():
    parsed = parse_sql("SELECT * FROM measurement", dialect="duckdb")
    cat = load_catalog("omop")
    _, reasons = assess_risk(parsed, cat, ScaleEstimate(output_records=1_500_000_000))
    joined = " ".join(reasons).lower()
    assert "high-volume event table" in joined
    assert "clinical event table" not in joined


def test_optimization_action_is_schema_neutral():
    parsed = parse_sql("SELECT * FROM measurement", dialect="duckdb")
    cat = load_catalog("omop")
    recs = recommend(parsed, cat, ScaleEstimate(output_records=1_500_000_000))
    actions = " ".join(r.action.lower() for r in recs)
    assert "selective filter" in actions
    assert "concept filter" not in actions
```

- [ ] **Step 2: Run it to verify it fails**

Run: `. .venv/bin/activate && python -m pytest tests/test_wording_neutral.py -q`
Expected: FAIL — current strings say "clinical event table" / "concept filter".

- [ ] **Step 3: Edit the wording**

In `preflight/risk.py`, change the unfiltered-event reason line:
```python
                reasons.append(f"Unfiltered scan of high-volume event table: {t} (no filter)")
```

In `preflight/optimize.py`, change the first recommendation's `action`:
```python
            action="Add a selective filter (e.g. a code or date predicate) on the event table",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && python -m pytest tests/test_wording_neutral.py tests/test_risk.py tests/test_optimize.py -q`
Expected: PASS (new wording tests + existing risk/optimize tests still green — they assert on "filter"/"measurement"/"no filter"/"unfiltered", which still hold).

- [ ] **Step 5: Commit**

```bash
git add preflight/risk.py preflight/optimize.py tests/test_wording_neutral.py
git commit -m "feat(preflight): schema-neutral risk/optimize wording (Epic-friendly)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Synthetic Epic T-SQL fixture + parse test

**Files:**
- Create: `fixtures/queries/epic_or_cases.sql`
- Test: `tests/test_parse_tsql_epic.py`

- [ ] **Step 1: Write the failing test**

`tests/test_parse_tsql_epic.py`:
```python
import pytest

from preflight.parse.sql import parse_sql, PreflightParseError
from fixtures.build_omop import load_query


def test_tsql_parses_epic_fixture():
    sql = load_query("epic_or_cases")
    parsed = parse_sql(sql, dialect="tsql")
    tables = set(parsed.base_tables)
    assert {"PATIENT_ENCOUNTER_DTL", "ALL_PATIENTS", "ORDER_PROCEDURE_DTL",
            "OR_CASE_KEY_XREF"} <= tables
    assert parsed.join_count >= 3


def test_generic_dialect_rejects_epic_fixture():
    sql = load_query("epic_or_cases")
    with pytest.raises(PreflightParseError):
        parse_sql(sql, dialect="generic")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `. .venv/bin/activate && python -m pytest tests/test_parse_tsql_epic.py -q`
Expected: FAIL — `FileNotFoundError` for `epic_or_cases.sql`.

- [ ] **Step 3: Write the fixture**

`fixtures/queries/epic_or_cases.sql`:
```sql
SELECT
  p.IDENT_ID AS 'MRN',
  CONVERT(varchar(10), e.HOSP_ADMSN_DT, 101) AS 'Admit Date',
  o.PROC_CD AS 'Procedure'
FROM dbo.PATIENT_ENCOUNTER_DTL e
JOIN dbo.ALL_PATIENTS p ON p.PATNT_KEY = e.PATNT_KEY
JOIN dbo.ORDER_PROCEDURE_DTL o ON o.PATNT_ENCNTR_KEY = e.PATNT_ENCNTR_KEY
JOIN dbo.OR_CASE_KEY_XREF x ON x.PATNT_ENCNTR_KEY = e.PATNT_ENCNTR_KEY
WHERE e.HOSP_ADMSN_DT >= CONVERT(datetime, '2024-01-01', 120)
  AND o.PROC_CD = '12345'
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && python -m pytest tests/test_parse_tsql_epic.py -q`
Expected: PASS (2 tests). If `generic` does NOT raise, the fixture's `CONVERT(varchar(10), …, 101)` (3-arg T-SQL form) is what forces the failure — confirm it is present and not simplified.

- [ ] **Step 5: Commit**

```bash
git add fixtures/queries/epic_or_cases.sql tests/test_parse_tsql_epic.py
git commit -m "test(preflight): synthetic Epic T-SQL fixture + dialect parse test

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Epic end-to-end pipeline test

**Files:**
- Test: `tests/test_pipeline_epic.py`

- [ ] **Step 1: Write the failing test (it should pass once everything is wired; this locks behavior)**

`tests/test_pipeline_epic.py`:
```python
from preflight.contracts import GeneratedSQL, RiskLevel
from preflight.catalog.loader import load_catalog
from preflight.pipeline import run_preflight
from fixtures.build_omop import load_query


def test_epic_clarity_pipeline_report():
    sql = GeneratedSQL(query=load_query("epic_or_cases"), dialect="tsql", target="clarity")
    report = run_preflight(sql, load_catalog("clarity"))

    assert report.summary.execution_target == "clarity"
    assert "ORDER_PROCEDURE_DTL" in report.summary.tables
    assert report.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert report.scale.encounters is not None          # encounter rollup populated (review M1)
    assert report.bottlenecks[0].component == "ORDER_PROCEDURE_DTL"  # highest-volume table
    assert report.optimizations                          # 1.2B * 0.001 = 1.2M output -> rec
```

- [ ] **Step 2: Run it to verify behavior**

Run: `. .venv/bin/activate && python -m pytest tests/test_pipeline_epic.py -q`
Expected: PASS. If `optimizations` is empty, confirm `clarity.yaml` has `ORDER_PROCEDURE_DTL.PROC_CD: 0.001` so estimated output (1.2B × 0.001 = 1.2M) clears the 500k recommendation threshold. If `risk_level` is below HIGH, confirm `ORDER_PROCEDURE_DTL.risk == very_high` in the catalog.

- [ ] **Step 3: (No new implementation — this validates Tasks 6–9 integrated.)**

If the test fails for a real reason, fix the responsible catalog/fixture file (not the test) and re-run.

- [ ] **Step 4: Commit**

```bash
git add tests/test_pipeline_epic.py
git commit -m "test(preflight): Epic Clarity end-to-end pipeline report

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: `.gitignore` `.env`, pyodbc optional dep note, README, full verification

**Files:**
- Create/Modify: `.gitignore`
- Modify: `requirements.txt`
- Modify: `README.md`

- [ ] **Step 1: Ensure `.env` and the user catalog dir are gitignored**

Append to `.gitignore` (create the file if absent):
```
.env
.preflight/
```

- [ ] **Step 2: Note the optional SQL Server driver in requirements.txt**

Append to `requirements.txt`:
```
# Optional, only for the Epic SQL Server catalog bootstrapper:
# pyodbc>=5.1
```

- [ ] **Step 3: Add an Epic section to README.md**

Append to `README.md`:
```markdown
## Epic EDW (T-SQL)

Analyze Epic Clarity/Caboodle SQL with the bundled catalogs (parsed as T-SQL):
```bash
preflight check my_clarity_report.sql --catalog clarity        # dialect defaults to tsql
```

### Generate a catalog from your own EDW (read-only)
The bootstrapper reads only system-catalog row-count stats (no data, no query execution):
```bash
# Postgres / DuckDB now; SQL Server (Epic) needs `pip install pyodbc`
preflight catalog-bootstrap --sqlserver-dsn "Driver={ODBC Driver 18 for SQL Server};Server=...;Database=Clarity;..." \
  --schema-name clarity --heuristic epic --stats-as-of 2026-06-01 \
  --out ~/.preflight/catalogs/clarity.yaml
```
Catalogs in `$PREFLIGHT_CATALOG_DIR` (default `~/.preflight/catalogs/`) override the bundled
seeds and are found automatically. Run the bootstrapper once per EDW refresh; day-to-day
`check` runs fully offline against the cached catalog.

### Config via `.env`
Put defaults in a (gitignored) `.env` so you don't repeat flags or expose credentials:
```
PREFLIGHT_CATALOG=clarity
PREFLIGHT_DIALECT=tsql
PREFLIGHT_SQLSERVER_DSN=Driver={ODBC Driver 18 for SQL Server};Server=...;Database=Clarity;...
```
```

- [ ] **Step 4: Run the entire suite + a CLI smoke test**

Run:
```bash
. .venv/bin/activate
python -m pytest -q
python -m preflight.cli check fixtures/queries/epic_or_cases.sql --catalog clarity
```
Expected: all tests pass (live PG/SQL Server tests skipped unless their env DSNs are set); the CLI prints a full report for the Epic fixture with `Risk: HIGH` or `CRITICAL` and `Runtime Category` populated.

- [ ] **Step 5: Commit**

```bash
git add .gitignore requirements.txt README.md
git commit -m "docs(preflight): gitignore .env, Epic README, optional pyodbc note

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review — Spec Coverage

- **Bootstrapper (spec §1)** → Tasks 1 (mapping), 2 (build/to_yaml), 3 (introspectors), 6 (`catalog-bootstrap` CLI). ✓
- **Catalog content & categories (spec §2)** → Task 7 seeds (reuse existing `encounter`/`clinical_event`/`demographics` vocabulary; review M1 resolved by explicit categories). ✓
- **Schema-neutral wording (spec §3, review S1)** → Task 8. ✓
- **CLI config: fixed catalog dir + `.env` + dialect default (spec §4, review S3/N1)** → Tasks 4 (loader resolution + `default_dialect`), 5 (`.env`), 6 (CLI). ✓
- **Synthetic fixture with CONVERT (spec §5, review M2)** → Task 9. ✓
- **Offline-first / freshness stamp** → `stats_as_of` in Tasks 2/6; README in Task 11. ✓
- **Privacy F2 (DSN off CLI)** → `.env` + env fallback in Tasks 5/6; `.gitignore` in Task 11. ✓
- **Tests** (catalog_epic, parse_tsql_epic, pipeline_epic, bootstrap*, config, resolution, cli_config) → Tasks 1–10. ✓
- **No UI** → out of scope; nothing builds UI. ✓

**Type/name consistency:** `TableStat(name,row_estimate,schema)`, `category_for/volume_for/risk_for`, `bootstrap_catalog(...)→dict`, `to_yaml(dict)→str`, `DuckDBIntrospector/PostgresIntrospector/SQLServerIntrospector.introspect()→List[TableStat]`, `load_catalog(schema, catalog_dir=None)`, `Catalog.default_dialect`, `load_dotenv/load_default_dotenvs` — all defined in early tasks and used consistently later. Existing engine signatures (`assess_risk`, `recommend`, `run_preflight`) unchanged.
