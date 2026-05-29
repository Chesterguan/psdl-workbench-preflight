# Preflight Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone, deterministic, zero-LLM SQL pre-execution analyzer that, given generated SQL + a metadata catalog (+ optional read-only DB connector), produces a structured Preflight report (lineage, scale, risk, bottlenecks, optimizations, live query plan, confidence) without executing the query.

**Architecture:** Self-contained Python package `preflight/` with its own contracts (never imports main-repo code). A pure pipeline of independently-testable stages: parse SQL → lineage → estimate → risk → bottleneck → optimize → confidence → report. The only optional/impure stage is a pluggable live `EXPLAIN` connector (DuckDB first, Postgres second). Cost engine uses a catalog heuristic baseline, refined by live plan facts when a connector is attached.

**Tech Stack:** Python 3.9, `sqlglot` (SQL parse/lineage), `duckdb`, `pydantic` v2, `pyyaml`, stdlib `argparse`, `pytest`. Optional `psycopg` for Postgres connector.

**Conventions for every task below:**
- Work from repo root `/Users/ziyuanguan/psdl-workbench-preflight` with the venv active: `. .venv/bin/activate` (created in Task 0).
- Run tests with `python -m pytest`.
- Commit messages end with the Co-Authored-By trailer used in Task 0.

---

### Task 0: Project scaffold

**Files:**
- Create: `requirements.txt`
- Create: `pyproject.toml`
- Create: `pytest.ini`
- Create: `preflight/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: Write the smoke test**

`tests/test_smoke.py`:
```python
def test_package_imports():
    import preflight
    assert preflight.__version__ == "0.1.0"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `. .venv/bin/activate && python -m pytest tests/test_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight'`

- [ ] **Step 3: Create the package and config files**

`preflight/__init__.py`:
```python
__version__ = "0.1.0"
```

`tests/__init__.py`: (empty file)

`requirements.txt`:
```
sqlglot==30.8.0
duckdb==1.4.4
pydantic==2.13.4
PyYAML==6.0.3
```

`pyproject.toml`:
```toml
[project]
name = "preflight"
version = "0.1.0"
description = "Deterministic SQL pre-execution analyzer for PSDL Workbench"
requires-python = ">=3.9"

[tool.setuptools.packages.find]
include = ["preflight*"]
```

`pytest.ini`:
```ini
[pytest]
testpaths = tests
addopts = -q
```

- [ ] **Step 4: Verify the venv has deps and the test passes**

Run: `. .venv/bin/activate && pip install -r requirements.txt -q && python -m pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add requirements.txt pyproject.toml pytest.ini preflight/__init__.py tests/__init__.py tests/test_smoke.py
git commit -m "chore(preflight): project scaffold + deps

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 1: Core contracts & enums

**Files:**
- Create: `preflight/contracts.py`
- Test: `tests/test_contracts.py`

These are the shared types every later task imports. `GeneratedSQL` is the input; `PreflightReport` holds the 8 sections. Uses pydantic v2 `BaseModel` so everything is JSON-serializable via `.model_dump()`.

- [ ] **Step 1: Write the failing test**

`tests/test_contracts.py`:
```python
from preflight.contracts import (
    GeneratedSQL, RiskLevel, Confidence, RuntimeCategory,
    PreflightReport, StudySummary, ScaleEstimate,
)


def test_generated_sql_defaults():
    g = GeneratedSQL(query="SELECT 1", dialect="duckdb")
    assert g.target == "generic"
    assert g.dialect == "duckdb"


def test_enums_have_expected_members():
    assert RiskLevel.CRITICAL.value == "CRITICAL"
    assert set(RuntimeCategory) == {
        RuntimeCategory.FAST, RuntimeCategory.MODERATE, RuntimeCategory.HEAVY,
        RuntimeCategory.EXTREME, RuntimeCategory.UNKNOWN,
    }
    assert set(Confidence) == {Confidence.LOW, Confidence.MEDIUM, Confidence.HIGH}


def test_report_is_json_serializable():
    report = PreflightReport(
        summary=StudySummary(execution_target="duckdb", tables=["person"],
                             domains=["demographics"], query_shape="1 join, 0 CTEs"),
        scale=ScaleEstimate(output_records=100, confidence=Confidence.MEDIUM),
        risk_level=RiskLevel.LOW,
        runtime_category=RuntimeCategory.FAST,
        confidence=Confidence.MEDIUM,
    )
    dumped = report.model_dump(mode="json")
    assert dumped["risk_level"] == "LOW"
    assert dumped["summary"]["execution_target"] == "duckdb"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_contracts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.contracts'`

- [ ] **Step 3: Write the implementation**

`preflight/contracts.py`:
```python
"""Shared contracts for the Preflight analyzer. No I/O, no LLM, no DB."""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ---- Input contract (mirrors main-repo SQLQuery shape; map via adapter at integration) ----
class GeneratedSQL(BaseModel):
    query: str
    dialect: str = "generic"        # sqlglot dialect: duckdb, postgres, tsql, ...
    target: str = "generic"         # schema family label: omop, epic, pcornet, generic


# ---- Enums ----
class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Confidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RuntimeCategory(str, Enum):
    FAST = "FAST"
    MODERATE = "MODERATE"
    HEAVY = "HEAVY"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"


# ---- §1 Study Summary ----
class StudySummary(BaseModel):
    execution_target: str
    tables: List[str] = Field(default_factory=list)
    domains: List[str] = Field(default_factory=list)
    query_shape: str = ""


# ---- §2 Lineage ----
class LineageNode(BaseModel):
    table: str
    category: str = "unknown"
    volume: str = "unknown"
    est_rows: Optional[int] = None


class LineageEdge(BaseModel):
    source: str
    target: str
    kind: str = "join"              # join | cte | filter
    cardinality_transition: str = ""  # e.g. "1:N (fan-out)"


class Lineage(BaseModel):
    nodes: List[LineageNode] = Field(default_factory=list)
    edges: List[LineageEdge] = Field(default_factory=list)
    filters: List[str] = Field(default_factory=list)


# ---- §3 Scale ----
class StageEstimate(BaseModel):
    name: str
    est_rows: int


class ScaleEstimate(BaseModel):
    patients: Optional[int] = None
    encounters: Optional[int] = None
    events: Optional[int] = None
    intermediate_records: Optional[int] = None
    output_records: Optional[int] = None
    per_stage: List[StageEstimate] = Field(default_factory=list)
    confidence: Confidence = Confidence.LOW


# ---- §5 Bottlenecks ----
class Bottleneck(BaseModel):
    component: str
    reason: str
    contribution_pct: int


# ---- §6 Optimizations ----
class Optimization(BaseModel):
    action: str
    rationale: str
    expected_benefit: str = ""


# ---- §7 Query plan (live connector only) ----
class PlanNode(BaseModel):
    op: str
    table: Optional[str] = None
    estimated_rows: Optional[int] = None
    scan_type: Optional[str] = None
    join_type: Optional[str] = None
    index_used: Optional[bool] = None


class QueryPlan(BaseModel):
    nodes: List[PlanNode] = Field(default_factory=list)
    total_estimated_rows: Optional[int] = None
    missing_index_hints: List[str] = Field(default_factory=list)


# ---- The full report ----
class PreflightReport(BaseModel):
    summary: StudySummary
    lineage: Lineage = Field(default_factory=Lineage)
    scale: ScaleEstimate
    risk_level: RiskLevel
    risk_reasons: List[str] = Field(default_factory=list)
    bottlenecks: List[Bottleneck] = Field(default_factory=list)
    optimizations: List[Optimization] = Field(default_factory=list)
    query_plan: Optional[QueryPlan] = None
    runtime_category: RuntimeCategory
    confidence: Confidence
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_contracts.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/contracts.py tests/test_contracts.py
git commit -m "feat(preflight): core contracts and enums

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: SQL parser

**Files:**
- Create: `preflight/parse/__init__.py` (empty)
- Create: `preflight/parse/sql.py`
- Test: `tests/test_parse_sql.py`

Parses SQL with sqlglot into a `ParsedSQL` with **base tables** (physical tables, excluding CTE names), CTE names, join count, equality-filter columns, and aggregation flag. Critical detail: `find_all(exp.Table)` returns CTE references too, so subtract CTE aliases.

- [ ] **Step 1: Write the failing test**

`tests/test_parse_sql.py`:
```python
from preflight.parse.sql import parse_sql

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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_parse_sql.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.parse'`

- [ ] **Step 3: Write the implementation**

`preflight/parse/__init__.py`: (empty file)

`preflight/parse/sql.py`:
```python
"""Deterministic SQL structural parser built on sqlglot."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import sqlglot
from sqlglot import exp


@dataclass
class ParsedSQL:
    base_tables: List[str] = field(default_factory=list)   # physical tables, sorted, deduped
    cte_names: List[str] = field(default_factory=list)
    join_count: int = 0
    filter_columns: List[str] = field(default_factory=list)
    has_aggregation: bool = False


_AGG_FUNCS = {"sum", "count", "avg", "min", "max", "median", "percentile_cont"}


def parse_sql(query: str, dialect: str = "generic") -> ParsedSQL:
    read = None if dialect in ("generic", "", None) else dialect
    tree = sqlglot.parse_one(query, read=read)

    cte_names = [cte.alias for cte in tree.find_all(exp.CTE)]
    cte_set = set(cte_names)

    all_tables = {t.name for t in tree.find_all(exp.Table)}
    base_tables = sorted(all_tables - cte_set)

    join_count = len(list(tree.find_all(exp.Join)))

    filter_columns = sorted({
        c.name for c in tree.find_all(exp.Column)
        if c.find_ancestor(exp.Where) is not None or c.find_ancestor(exp.Join) is not None
    })

    has_aggregation = (
        tree.find(exp.Group) is not None
        or any(f.sql_name().lower() in _AGG_FUNCS for f in tree.find_all(exp.Func))
    )

    return ParsedSQL(
        base_tables=base_tables,
        cte_names=sorted(cte_names),
        join_count=join_count,
        filter_columns=filter_columns,
        has_aggregation=has_aggregation,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_parse_sql.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/parse/ tests/test_parse_sql.py
git commit -m "feat(preflight): sqlglot-based SQL structural parser

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Catalog loader + seed OMOP catalog

**Files:**
- Create: `preflight/catalog/__init__.py` (empty)
- Create: `preflight/catalog/loader.py`
- Create: `preflight/catalog/schemas/omop.yaml`
- Test: `tests/test_catalog.py`

Loads a schema-family YAML into a `Catalog`. Provides `profile(table)` that returns the table profile, or a generic fallback (volume/risk unknown) for unknown tables — and reports whether a fallback was used (drives confidence later).

- [ ] **Step 1: Write the failing test**

`tests/test_catalog.py`:
```python
from preflight.catalog.loader import load_catalog, TableProfile


def test_load_omop_and_lookup_known_table():
    cat = load_catalog("omop")
    prof = cat.profile("measurement")
    assert isinstance(prof, TableProfile)
    assert prof.category == "clinical_event"
    assert prof.volume == "huge"
    assert prof.row_estimate and prof.row_estimate > 1_000_000
    assert cat.is_known("measurement") is True


def test_unknown_table_returns_generic_fallback():
    cat = load_catalog("omop")
    prof = cat.profile("some_random_table")
    assert prof.volume == "unknown"
    assert prof.risk == "unknown"
    assert cat.is_known("some_random_table") is False


def test_join_fanout_lookup():
    cat = load_catalog("omop")
    assert cat.join_fanout("person", "measurement") == "high"
    assert cat.join_fanout("person", "nonexistent") == "unknown"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.catalog'`

- [ ] **Step 3: Write the implementation and seed catalog**

`preflight/catalog/__init__.py`: (empty file)

`preflight/catalog/schemas/omop.yaml`:
```yaml
# Seed OMOP CDM catalog. Row estimates are illustrative institution-scale defaults.
schema: omop
tables:
  person:
    category: demographics
    volume: medium
    risk: low
    row_estimate: 2000000
  visit_occurrence:
    category: encounter
    volume: large
    risk: medium
    row_estimate: 12000000
  measurement:
    category: clinical_event
    volume: huge
    risk: very_high
    row_estimate: 1500000000
  observation:
    category: clinical_event
    volume: huge
    risk: high
    row_estimate: 800000000
  drug_exposure:
    category: clinical_event
    volume: huge
    risk: high
    row_estimate: 600000000
  condition_occurrence:
    category: clinical_event
    volume: large
    risk: medium
    row_estimate: 300000000
joins:
  person->visit_occurrence: high
  person->measurement: high
  person->observation: high
  person->drug_exposure: high
  visit_occurrence->measurement: high
columns:
  measurement.measurement_concept_id: 0.0005
  observation.observation_concept_id: 0.0008
  drug_exposure.drug_concept_id: 0.001
```

`preflight/catalog/loader.py`:
```python
"""Load schema-family metadata catalogs (pure data). No DB access."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional

import yaml

_SCHEMAS_DIR = os.path.join(os.path.dirname(__file__), "schemas")

_VOLUME_ROWS = {  # fallback row estimates when row_estimate is absent
    "tiny": 1_000,
    "small": 100_000,
    "medium": 2_000_000,
    "large": 50_000_000,
    "huge": 1_000_000_000,
    "unknown": 1_000_000,
}


@dataclass
class TableProfile:
    name: str
    category: str = "unknown"
    volume: str = "unknown"
    risk: str = "unknown"
    row_estimate: Optional[int] = None

    def effective_rows(self) -> int:
        if self.row_estimate is not None:
            return self.row_estimate
        return _VOLUME_ROWS.get(self.volume, _VOLUME_ROWS["unknown"])


class Catalog:
    def __init__(self, schema: str, tables: Dict[str, TableProfile],
                 joins: Dict[str, str], columns: Dict[str, float]):
        self.schema = schema
        self._tables = tables
        self._joins = joins
        self._columns = columns

    def is_known(self, table: str) -> bool:
        return table.lower() in self._tables

    def profile(self, table: str) -> TableProfile:
        key = table.lower()
        if key in self._tables:
            return self._tables[key]
        return TableProfile(name=table)  # generic fallback (all "unknown")

    def join_fanout(self, source: str, target: str) -> str:
        return self._joins.get(f"{source.lower()}->{target.lower()}", "unknown")

    def selectivity(self, column: str) -> Optional[float]:
        return self._columns.get(column.lower())


def load_catalog(schema: str) -> Catalog:
    path = os.path.join(_SCHEMAS_DIR, f"{schema}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(f"No catalog for schema '{schema}' at {path}")
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
    return Catalog(schema=data.get("schema", schema), tables=tables, joins=joins, columns=columns)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_catalog.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/catalog/ tests/test_catalog.py
git commit -m "feat(preflight): catalog loader + seed OMOP catalog

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Lineage engine

**Files:**
- Create: `preflight/lineage.py`
- Test: `tests/test_lineage.py`

Builds a `Lineage` (§2) from `ParsedSQL` + `Catalog`: one node per base table (annotated with category/volume/est_rows), edges for joins with a cardinality-transition label derived from catalog join fan-out, and the WHERE filter columns.

- [ ] **Step 1: Write the failing test**

`tests/test_lineage.py`:
```python
from preflight.parse.sql import parse_sql
from preflight.catalog.loader import load_catalog
from preflight.lineage import build_lineage

SQL = """
SELECT p.person_id, m.value_as_number
FROM person p
JOIN measurement m ON p.person_id = m.person_id
WHERE m.measurement_concept_id = 3016723
"""


def test_lineage_nodes_annotated_from_catalog():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    lin = build_lineage(parsed, cat)
    by_table = {n.table: n for n in lin.nodes}
    assert by_table["measurement"].volume == "huge"
    assert by_table["measurement"].est_rows == 1_500_000_000
    assert by_table["person"].category == "demographics"


def test_lineage_edges_have_cardinality_transition():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    lin = build_lineage(parsed, cat)
    assert len(lin.edges) == 1
    edge = lin.edges[0]
    assert "fan-out" in edge.cardinality_transition.lower()


def test_lineage_captures_filters():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    lin = build_lineage(parsed, cat)
    assert "measurement_concept_id" in lin.filters
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_lineage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.lineage'`

- [ ] **Step 3: Write the implementation**

`preflight/lineage.py`:
```python
"""§2 Data lineage: tables, join edges, cardinality transitions, filters."""
from __future__ import annotations

from preflight.catalog.loader import Catalog
from preflight.contracts import Lineage, LineageEdge, LineageNode
from preflight.parse.sql import ParsedSQL

_FANOUT_LABEL = {
    "high": "1:N (fan-out)",
    "medium": "1:N (moderate fan-out)",
    "low": "~1:1",
    "unknown": "unknown",
}


def build_lineage(parsed: ParsedSQL, catalog: Catalog) -> Lineage:
    nodes = []
    for table in parsed.base_tables:
        prof = catalog.profile(table)
        nodes.append(LineageNode(
            table=table,
            category=prof.category,
            volume=prof.volume,
            est_rows=prof.effective_rows() if catalog.is_known(table) else None,
        ))

    # Chain edges across base tables in declared order, labeling by catalog fan-out.
    edges = []
    tables = parsed.base_tables
    for i in range(len(tables) - 1):
        src, tgt = tables[i], tables[i + 1]
        fanout = catalog.join_fanout(src, tgt)
        if fanout == "unknown":
            fanout = catalog.join_fanout(tgt, src)
        edges.append(LineageEdge(
            source=src, target=tgt, kind="join",
            cardinality_transition=_FANOUT_LABEL.get(fanout, "unknown"),
        ))

    return Lineage(nodes=nodes, edges=edges, filters=list(parsed.filter_columns))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_lineage.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/lineage.py tests/test_lineage.py
git commit -m "feat(preflight): lineage engine (nodes, join edges, cardinality)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Cost / scale estimator

**Files:**
- Create: `preflight/estimate.py`
- Test: `tests/test_estimate.py`

Catalog heuristic baseline (Approach B, layer 1): driver table = largest base table by est_rows. Apply filter selectivity (product of known column selectivities, else a default per filtered table) and per-stage propagation. Map output size → `RuntimeCategory`. Accepts optional `plan_rows` (from a live connector) to override the output estimate (layer 2). Returns `(ScaleEstimate, RuntimeCategory)`.

- [ ] **Step 1: Write the failing test**

`tests/test_estimate.py`:
```python
from preflight.parse.sql import parse_sql
from preflight.catalog.loader import load_catalog
from preflight.contracts import Confidence, RuntimeCategory
from preflight.estimate import estimate_scale

SQL = """
SELECT p.person_id, m.value_as_number
FROM person p
JOIN measurement m ON p.person_id = m.person_id
WHERE m.measurement_concept_id = 3016723
"""


def test_estimate_uses_largest_table_as_driver():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    scale, runtime = estimate_scale(parsed, cat, catalog_known_ratio=1.0)
    # measurement (1.5B) is the driver; concept filter selectivity 0.0005 shrinks it
    assert scale.events == 1_500_000_000
    assert 0 < scale.output_records < 1_500_000_000
    assert scale.confidence == Confidence.MEDIUM
    assert runtime in set(RuntimeCategory)


def test_plan_rows_override_takes_precedence():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    scale, _ = estimate_scale(parsed, cat, catalog_known_ratio=1.0, plan_rows=42)
    assert scale.output_records == 42
    assert scale.confidence == Confidence.HIGH


def test_runtime_category_thresholds():
    from preflight.estimate import runtime_for_rows
    assert runtime_for_rows(5_000) == RuntimeCategory.FAST
    assert runtime_for_rows(500_000) == RuntimeCategory.MODERATE
    assert runtime_for_rows(20_000_000) == RuntimeCategory.HEAVY
    assert runtime_for_rows(5_000_000_000) == RuntimeCategory.EXTREME
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_estimate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.estimate'`

- [ ] **Step 3: Write the implementation**

`preflight/estimate.py`:
```python
"""§3 Scale + cost engine. Catalog heuristic baseline, optional live-plan override."""
from __future__ import annotations

from typing import Optional, Tuple

from preflight.catalog.loader import Catalog
from preflight.contracts import Confidence, RuntimeCategory, ScaleEstimate, StageEstimate
from preflight.parse.sql import ParsedSQL

# Output-row thresholds (rows) -> runtime category. Deterministic, documented heuristic.
_FAST_MAX = 100_000
_MODERATE_MAX = 5_000_000
_HEAVY_MAX = 1_000_000_000
_DEFAULT_FILTER_SELECTIVITY = 0.1  # applied once if a table is filtered but no column hint


def runtime_for_rows(rows: int) -> RuntimeCategory:
    if rows < _FAST_MAX:
        return RuntimeCategory.FAST
    if rows < _MODERATE_MAX:
        return RuntimeCategory.MODERATE
    if rows < _HEAVY_MAX:
        return RuntimeCategory.HEAVY
    return RuntimeCategory.EXTREME


def _selectivity(parsed: ParsedSQL, catalog: Catalog) -> float:
    """Product of known column selectivities; else one default factor if any filter exists."""
    factors = []
    for col in parsed.filter_columns:
        # filter_columns are bare names; try every base table qualifier
        for tbl in parsed.base_tables:
            sel = catalog.selectivity(f"{tbl}.{col}")
            if sel is not None:
                factors.append(sel)
                break
    if factors:
        result = 1.0
        for f in factors:
            result *= f
        return result
    return _DEFAULT_FILTER_SELECTIVITY if parsed.filter_columns else 1.0


def estimate_scale(
    parsed: ParsedSQL,
    catalog: Catalog,
    catalog_known_ratio: float,
    plan_rows: Optional[int] = None,
) -> Tuple[ScaleEstimate, RuntimeCategory]:
    known_tables = [t for t in parsed.base_tables if catalog.is_known(t)]
    rows_by_table = {t: catalog.profile(t).effective_rows() for t in parsed.base_tables}

    # Driver = largest base table by estimated rows.
    driver_rows = max(rows_by_table.values()) if rows_by_table else 0

    # Categorize the canonical clinical entities by catalog category, for the report.
    def _rows_for_category(cat_name: str) -> Optional[int]:
        vals = [rows_by_table[t] for t in parsed.base_tables
                if catalog.profile(t).category == cat_name]
        return max(vals) if vals else None

    patients = _rows_for_category("demographics")
    encounters = _rows_for_category("encounter")
    events = _rows_for_category("clinical_event")

    selectivity = _selectivity(parsed, catalog)
    intermediate = int(driver_rows)
    output = int(driver_rows * selectivity)
    if parsed.has_aggregation and patients:
        output = min(output, patients)  # GROUP BY person collapses to ~#patients

    per_stage = [
        StageEstimate(name="driver_scan", est_rows=int(driver_rows)),
        StageEstimate(name="after_filter", est_rows=output),
    ]

    confidence = Confidence.LOW if catalog_known_ratio < 0.5 else Confidence.MEDIUM
    if plan_rows is not None:
        output = int(plan_rows)
        confidence = Confidence.HIGH

    scale = ScaleEstimate(
        patients=patients,
        encounters=encounters,
        events=events,
        intermediate_records=intermediate,
        output_records=output,
        per_stage=per_stage,
        confidence=confidence,
    )
    return scale, runtime_for_rows(output)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_estimate.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/estimate.py tests/test_estimate.py
git commit -m "feat(preflight): scale + cost estimator (catalog baseline, plan override)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Risk engine

**Files:**
- Create: `preflight/risk.py`
- Test: `tests/test_risk.py`

Deterministic rules → `(RiskLevel, reasons)`. Inputs: parsed SQL, catalog, scale. Signals: very-high-risk table touched, large output cardinality, join depth, missing WHERE filter on a clinical-event table.

- [ ] **Step 1: Write the failing test**

`tests/test_risk.py`:
```python
from preflight.parse.sql import parse_sql
from preflight.catalog.loader import load_catalog
from preflight.contracts import RiskLevel, ScaleEstimate
from preflight.risk import assess_risk


def _scale(output):
    return ScaleEstimate(output_records=output)


def test_high_risk_table_and_large_output():
    parsed = parse_sql(
        "SELECT * FROM measurement WHERE measurement_concept_id = 3016723", dialect="duckdb")
    cat = load_catalog("omop")
    level, reasons = assess_risk(parsed, cat, _scale(2_000_000))
    assert level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    assert any("measurement" in r.lower() for r in reasons)


def test_low_risk_small_demographics_query():
    parsed = parse_sql("SELECT person_id FROM person WHERE person_id = 5", dialect="duckdb")
    cat = load_catalog("omop")
    level, reasons = assess_risk(parsed, cat, _scale(1))
    assert level == RiskLevel.LOW


def test_unfiltered_clinical_event_table_is_critical():
    parsed = parse_sql("SELECT * FROM measurement", dialect="duckdb")
    cat = load_catalog("omop")
    level, reasons = assess_risk(parsed, cat, _scale(1_500_000_000))
    assert level == RiskLevel.CRITICAL
    assert any("no filter" in r.lower() or "unfiltered" in r.lower() for r in reasons)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_risk.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.risk'`

- [ ] **Step 3: Write the implementation**

`preflight/risk.py`:
```python
"""§4 Execution risk. Deterministic rule-based scoring."""
from __future__ import annotations

from typing import List, Tuple

from preflight.catalog.loader import Catalog
from preflight.contracts import RiskLevel, ScaleEstimate
from preflight.parse.sql import ParsedSQL

_RISK_SCORE = {"very_high": 3, "high": 2, "medium": 1, "low": 0, "unknown": 1}
_ORDER = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]


def assess_risk(parsed: ParsedSQL, catalog: Catalog,
                scale: ScaleEstimate) -> Tuple[RiskLevel, List[str]]:
    score = 0
    reasons: List[str] = []

    # 1) Most expensive table touched.
    for t in parsed.base_tables:
        prof = catalog.profile(t)
        pts = _RISK_SCORE.get(prof.risk, 1)
        if pts >= 2:
            score += pts
            reasons.append(f"{prof.risk.replace('_', ' ').title()} risk table: {t}")

    # 2) Output cardinality.
    out = scale.output_records or 0
    if out >= 1_000_000_000:
        score += 3
        reasons.append(f"Very large output cardinality (~{out:,} rows)")
    elif out >= 1_000_000:
        score += 2
        reasons.append(f"Large output cardinality (~{out:,} rows)")

    # 3) Join depth.
    if parsed.join_count >= 4:
        score += 2
        reasons.append(f"Deep join chain ({parsed.join_count} joins)")
    elif parsed.join_count >= 2:
        score += 1

    # 4) Unfiltered clinical-event table.
    if not parsed.filter_columns:
        for t in parsed.base_tables:
            if catalog.profile(t).category == "clinical_event":
                score += 3
                reasons.append(f"Unfiltered scan of clinical event table: {t} (no filter)")
                break

    if score >= 6:
        level = RiskLevel.CRITICAL
    elif score >= 4:
        level = RiskLevel.HIGH
    elif score >= 2:
        level = RiskLevel.MEDIUM
    else:
        level = RiskLevel.LOW

    if not reasons:
        reasons.append("Small, well-filtered query over low-risk tables")
    return level, reasons
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_risk.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/risk.py tests/test_risk.py
git commit -m "feat(preflight): risk engine (rule-based LOW..CRITICAL)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Bottleneck analysis

**Files:**
- Create: `preflight/bottleneck.py`
- Test: `tests/test_bottleneck.py`

Ranks base tables by estimated row contribution → `List[Bottleneck]` with integer percentages that sum to ~100. Reason text from catalog category/volume.

- [ ] **Step 1: Write the failing test**

`tests/test_bottleneck.py`:
```python
from preflight.parse.sql import parse_sql
from preflight.catalog.loader import load_catalog
from preflight.bottleneck import find_bottlenecks

SQL = """
SELECT p.person_id, m.value_as_number
FROM person p
JOIN measurement m ON p.person_id = m.person_id
JOIN visit_occurrence v ON v.person_id = p.person_id
"""


def test_largest_table_is_primary_bottleneck():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    bns = find_bottlenecks(parsed, cat)
    assert bns[0].component == "measurement"
    assert bns[0].contribution_pct >= bns[1].contribution_pct
    assert bns[0].contribution_pct > 50


def test_contributions_sum_to_about_100():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    bns = find_bottlenecks(parsed, cat)
    total = sum(b.contribution_pct for b in bns)
    assert 98 <= total <= 102
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_bottleneck.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.bottleneck'`

- [ ] **Step 3: Write the implementation**

`preflight/bottleneck.py`:
```python
"""§5 Bottleneck analysis: rank tables by estimated row contribution."""
from __future__ import annotations

from typing import List

from preflight.catalog.loader import Catalog
from preflight.contracts import Bottleneck
from preflight.parse.sql import ParsedSQL


def find_bottlenecks(parsed: ParsedSQL, catalog: Catalog) -> List[Bottleneck]:
    rows = {t: catalog.profile(t).effective_rows() for t in parsed.base_tables}
    total = sum(rows.values()) or 1
    ranked = sorted(rows.items(), key=lambda kv: kv[1], reverse=True)

    result = []
    for table, r in ranked:
        prof = catalog.profile(table)
        pct = round(100 * r / total)
        reason = f"{prof.category.replace('_', ' ')} table, volume={prof.volume}"
        result.append(Bottleneck(component=table, reason=reason, contribution_pct=pct))

    # Fix rounding drift so percentages sum to 100 (adjust the largest).
    drift = 100 - sum(b.contribution_pct for b in result)
    if result and drift != 0:
        result[0].contribution_pct += drift
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_bottleneck.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/bottleneck.py tests/test_bottleneck.py
git commit -m "feat(preflight): bottleneck analysis (ranked contribution)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Optimization recommendations

**Files:**
- Create: `preflight/optimize.py`
- Test: `tests/test_optimize.py`

Maps detected conditions → structured `Optimization` items (the deterministic dry-run signal the workbench could later consume). Conditions: unfiltered clinical-event table, broad output, deep joins, missing concept filter.

- [ ] **Step 1: Write the failing test**

`tests/test_optimize.py`:
```python
from preflight.parse.sql import parse_sql
from preflight.catalog.loader import load_catalog
from preflight.contracts import ScaleEstimate
from preflight.optimize import recommend


def test_unfiltered_event_table_recommends_filter():
    parsed = parse_sql("SELECT * FROM measurement", dialect="duckdb")
    cat = load_catalog("omop")
    recs = recommend(parsed, cat, ScaleEstimate(output_records=1_500_000_000))
    actions = " ".join(r.action.lower() for r in recs)
    assert "filter" in actions or "concept" in actions
    assert all(r.action for r in recs)  # no empty actions


def test_small_clean_query_has_no_recommendations():
    parsed = parse_sql("SELECT person_id FROM person WHERE person_id = 5", dialect="duckdb")
    cat = load_catalog("omop")
    recs = recommend(parsed, cat, ScaleEstimate(output_records=1))
    assert recs == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_optimize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.optimize'`

- [ ] **Step 3: Write the implementation**

`preflight/optimize.py`:
```python
"""§6 Optimization recommendations. Structured, deterministic. No LLM."""
from __future__ import annotations

from typing import List

from preflight.catalog.loader import Catalog
from preflight.contracts import Optimization, ScaleEstimate
from preflight.parse.sql import ParsedSQL


def recommend(parsed: ParsedSQL, catalog: Catalog,
              scale: ScaleEstimate) -> List[Optimization]:
    recs: List[Optimization] = []
    event_tables = [t for t in parsed.base_tables
                    if catalog.profile(t).category == "clinical_event"]

    if event_tables and not parsed.filter_columns:
        recs.append(Optimization(
            action="Add a concept filter on the clinical event table",
            rationale=f"Unfiltered scan of {', '.join(event_tables)} reads the entire table.",
            expected_benefit="Often >90% fewer scanned rows when filtering to specific concepts.",
        ))

    out = scale.output_records or 0
    if out >= 1_000_000:
        recs.append(Optimization(
            action="Build a cohort prefilter before joining event tables",
            rationale=f"Estimated output is large (~{out:,} rows).",
            expected_benefit="Estimated 60% reduction in scanned records.",
        ))

    if parsed.join_count >= 4:
        recs.append(Optimization(
            action="Reduce join depth or stage intermediate results",
            rationale=f"{parsed.join_count} joins increase intermediate cardinality.",
            expected_benefit="Lower peak memory and intermediate row counts.",
        ))

    if len(event_tables) >= 2:
        recs.append(Optimization(
            action="Narrow the observation scope to required domains",
            rationale=f"Multiple high-volume event tables touched: {', '.join(event_tables)}.",
            expected_benefit="Fewer tables scanned; smaller intermediate sets.",
        ))

    return recs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_optimize.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/optimize.py tests/test_optimize.py
git commit -m "feat(preflight): optimization recommendations (structured)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Confidence scoring

**Files:**
- Create: `preflight/confidence.py`
- Test: `tests/test_confidence.py`

`score_confidence(known_ratio, has_plan) -> Confidence`: HIGH if a live plan is present AND most tables are cataloged; MEDIUM if catalog coverage is good but no plan; LOW if catalog coverage is sparse.

- [ ] **Step 1: Write the failing test**

`tests/test_confidence.py`:
```python
from preflight.contracts import Confidence
from preflight.confidence import score_confidence


def test_high_confidence_with_plan_and_full_catalog():
    assert score_confidence(known_ratio=1.0, has_plan=True) == Confidence.HIGH


def test_medium_confidence_full_catalog_no_plan():
    assert score_confidence(known_ratio=1.0, has_plan=False) == Confidence.MEDIUM


def test_low_confidence_sparse_catalog():
    assert score_confidence(known_ratio=0.2, has_plan=False) == Confidence.LOW
    assert score_confidence(known_ratio=0.2, has_plan=True) == Confidence.MEDIUM
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_confidence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.confidence'`

- [ ] **Step 3: Write the implementation**

`preflight/confidence.py`:
```python
"""§8 Confidence score from catalog coverage and live-plan availability."""
from __future__ import annotations

from preflight.contracts import Confidence


def score_confidence(known_ratio: float, has_plan: bool) -> Confidence:
    good_catalog = known_ratio >= 0.7
    if has_plan and good_catalog:
        return Confidence.HIGH
    if has_plan or good_catalog:
        return Confidence.MEDIUM
    return Confidence.LOW
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_confidence.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/confidence.py tests/test_confidence.py
git commit -m "feat(preflight): confidence scoring

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Connector interface + DuckDB connector

**Files:**
- Create: `preflight/connector/__init__.py` (empty)
- Create: `preflight/connector/base.py`
- Create: `preflight/connector/duckdb_connector.py`
- Test: `tests/test_connector_duckdb.py`

Defines the `Connector` protocol and `PlanFacts` dataclass, plus a DuckDB implementation that issues `EXPLAIN` (never `EXPLAIN ANALYZE` — no execution) and parses the text plan for node types, join types, scan types, and `~N rows` estimates.

- [ ] **Step 1: Write the failing test**

`tests/test_connector_duckdb.py`:
```python
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
    assert any(n.join_type for n in facts.nodes)        # a join node detected
    assert any(n.scan_type for n in facts.nodes)         # scan nodes detected


def test_connector_never_runs_analyze(con):
    # Guard: a SELECT that would error if executed should still EXPLAIN fine,
    # because EXPLAIN only plans. Use a divide-by-zero in projection.
    conn = DuckDBConnector(con)
    facts = conn.analyze("SELECT person_id / 0 AS bad FROM person")
    assert facts is not None  # did not raise -> not executed
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_connector_duckdb.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.connector'`

- [ ] **Step 3: Write the implementation**

`preflight/connector/__init__.py`: (empty file)

`preflight/connector/base.py`:
```python
"""Live connector interface. Implementations issue EXPLAIN-class statements only."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol

from preflight.contracts import PlanNode, QueryPlan


@dataclass
class PlanFacts:
    nodes: List[PlanNode] = field(default_factory=list)
    total_estimated_rows: Optional[int] = None
    missing_index_hints: List[str] = field(default_factory=list)

    def to_query_plan(self) -> QueryPlan:
        return QueryPlan(
            nodes=self.nodes,
            total_estimated_rows=self.total_estimated_rows,
            missing_index_hints=self.missing_index_hints,
        )


class Connector(Protocol):
    def analyze(self, sql: str) -> PlanFacts:
        """Return plan facts WITHOUT executing the query."""
        ...
```

`preflight/connector/duckdb_connector.py`:
```python
"""DuckDB live connector. Parses text EXPLAIN (no execution)."""
from __future__ import annotations

import re
from typing import List, Optional

from preflight.connector.base import PlanFacts
from preflight.contracts import PlanNode

_ROWS_RE = re.compile(r"~([\d,]+)\s+rows")
_NODE_RE = re.compile(r"^\s*│?\s*([A-Z_]{3,})\s*$")
_JOIN_TYPE_RE = re.compile(r"Join Type:\s*([A-Z]+)")
_TABLE_RE = re.compile(r"Table:\s*([A-Za-z0-9_]+)")


class DuckDBConnector:
    def __init__(self, connection):
        self._con = connection

    def analyze(self, sql: str) -> PlanFacts:
        rows = self._con.execute("EXPLAIN " + sql).fetchall()
        text = "\n".join(r[1] for r in rows if len(r) > 1)
        return _parse_text_plan(text)


def _parse_text_plan(text: str) -> PlanFacts:
    nodes: List[PlanNode] = []
    total: Optional[int] = None
    lines = text.splitlines()

    current_op: Optional[str] = None
    for line in lines:
        node_m = _NODE_RE.match(line.replace("─", "").rstrip())
        if node_m and node_m.group(1) not in ("INNER", "OUTER", "LEFT", "RIGHT"):
            current_op = node_m.group(1)
            node = PlanNode(op=current_op)
            if "SCAN" in current_op:
                node.scan_type = current_op
            if "JOIN" in current_op:
                node.join_type = "INNER"  # refined below if explicit
            nodes.append(node)

        if nodes:
            jt = _JOIN_TYPE_RE.search(line)
            if jt:
                nodes[-1].join_type = jt.group(1)
            tb = _TABLE_RE.search(line)
            if tb:
                nodes[-1].table = tb.group(1)
            rm = _ROWS_RE.search(line)
            if rm:
                val = int(rm.group(1).replace(",", ""))
                nodes[-1].estimated_rows = val
                if total is None:
                    total = val  # top-most node's estimate ~ output rows

    return PlanFacts(nodes=nodes, total_estimated_rows=total)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_connector_duckdb.py -v`
Expected: PASS (2 tests). If the node regex misses your DuckDB version's box-drawing, adjust `_NODE_RE` to also strip leading box characters `┌│└├┤┐┘─ ` before matching — the test asserts only that join/scan nodes and a total estimate are found.

- [ ] **Step 5: Commit**

```bash
git add preflight/connector/ tests/test_connector_duckdb.py
git commit -m "feat(preflight): connector interface + DuckDB EXPLAIN parser

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Synthetic OMOP DuckDB fixture

**Files:**
- Create: `fixtures/__init__.py` (empty)
- Create: `fixtures/build_omop.py`
- Create: `fixtures/queries/crrt_flowsheet.sql`
- Test: `tests/test_fixture_omop.py`

A deterministic builder that creates an in-memory (or file) OMOP-shaped DuckDB with person/visit_occurrence/measurement and a sample query. Used by the end-to-end pipeline test. Deterministic: seed the RNG via `setseed` so row counts are stable. (Note: `Math.random`/`Date.now` are not relevant here — this is DuckDB SQL, not the workflow runtime.)

- [ ] **Step 1: Write the failing test**

`tests/test_fixture_omop.py`:
```python
from fixtures.build_omop import build_omop_duckdb


def test_fixture_has_expected_tables_and_rows():
    con = build_omop_duckdb()
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert {"person", "visit_occurrence", "measurement"} <= tables
    n = con.execute("SELECT COUNT(*) FROM measurement").fetchone()[0]
    assert n > 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_fixture_omop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'fixtures.build_omop'`

- [ ] **Step 3: Write the implementation**

`fixtures/__init__.py`: (empty file)

`fixtures/queries/crrt_flowsheet.sql`:
```sql
SELECT p.person_id, m.value_as_number, m.measurement_date
FROM person p
JOIN measurement m ON p.person_id = m.person_id
WHERE m.measurement_concept_id = 3016723
  AND m.measurement_date BETWEEN DATE '2024-01-01' AND DATE '2024-03-31'
```

`fixtures/build_omop.py`:
```python
"""Build a small, deterministic OMOP-shaped DuckDB for testing. No network."""
from __future__ import annotations

import os

import duckdb

QUERY_DIR = os.path.join(os.path.dirname(__file__), "queries")


def build_omop_duckdb(path: str = ":memory:"):
    con = duckdb.connect(path)
    con.execute("SELECT setseed(0.42)")  # deterministic RNG
    con.execute("CREATE TABLE person(person_id INTEGER, year_of_birth INTEGER)")
    con.execute("CREATE TABLE visit_occurrence("
                "visit_occurrence_id INTEGER, person_id INTEGER, visit_concept_id INTEGER)")
    con.execute("CREATE TABLE measurement("
                "measurement_id INTEGER, person_id INTEGER, measurement_concept_id INTEGER, "
                "value_as_number DOUBLE, measurement_date DATE)")

    con.execute("INSERT INTO person SELECT range, 1950 + (range % 50) FROM range(500)")
    con.execute("INSERT INTO visit_occurrence "
                "SELECT range, range % 500, 9201 FROM range(1200)")
    con.execute(
        "INSERT INTO measurement SELECT range, range % 500, 3016723, "
        "10 + 5 * random(), DATE '2024-01-01' + INTERVAL (range % 120) DAY "
        "FROM range(40000)")
    return con


def load_query(name: str) -> str:
    with open(os.path.join(QUERY_DIR, f"{name}.sql"), "r") as fh:
        return fh.read()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_fixture_omop.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Commit**

```bash
git add fixtures/ tests/test_fixture_omop.py
git commit -m "test(preflight): synthetic OMOP DuckDB fixture + sample query

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Pipeline orchestration

**Files:**
- Create: `preflight/pipeline.py`
- Modify: `preflight/__init__.py` (export `run_preflight`)
- Test: `tests/test_pipeline.py`

Wires all stages into `run_preflight(sql, catalog, connector=None) -> PreflightReport`. Computes `catalog_known_ratio`, builds the §1 summary from parsed SQL, and uses the connector (if any) to override scale + populate `query_plan`.

- [ ] **Step 1: Write the failing test**

`tests/test_pipeline.py`:
```python
from preflight.contracts import GeneratedSQL, Confidence, RiskLevel
from preflight.catalog.loader import load_catalog
from preflight.connector.duckdb_connector import DuckDBConnector
from preflight.pipeline import run_preflight
from fixtures.build_omop import build_omop_duckdb, load_query


def test_offline_pipeline_produces_all_sections():
    sql = GeneratedSQL(query=load_query("crrt_flowsheet"), dialect="duckdb", target="omop")
    cat = load_catalog("omop")
    report = run_preflight(sql, cat)

    assert report.summary.execution_target == "omop"
    assert "measurement" in report.summary.tables
    assert report.scale.output_records is not None
    assert report.risk_level in set(RiskLevel)
    assert report.bottlenecks and report.bottlenecks[0].component == "measurement"
    assert report.optimizations  # filtered concept query still large -> cohort prefilter
    assert report.query_plan is None             # no connector
    assert report.confidence == Confidence.MEDIUM  # full catalog, no plan


def test_pipeline_with_connector_populates_plan_and_high_confidence():
    con = build_omop_duckdb()
    sql = GeneratedSQL(query=load_query("crrt_flowsheet"), dialect="duckdb", target="omop")
    cat = load_catalog("omop")
    report = run_preflight(sql, cat, connector=DuckDBConnector(con))

    assert report.query_plan is not None
    assert report.confidence == Confidence.HIGH
    # live plan overrides the heuristic output estimate
    assert report.scale.confidence == Confidence.HIGH
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_preflight'`

- [ ] **Step 3: Write the implementation**

`preflight/pipeline.py`:
```python
"""Orchestrates the deterministic Preflight pipeline. The connector is the only
optional/impure dependency. No LLM anywhere."""
from __future__ import annotations

from typing import Optional

from preflight.bottleneck import find_bottlenecks
from preflight.catalog.loader import Catalog
from preflight.confidence import score_confidence
from preflight.connector.base import Connector
from preflight.contracts import GeneratedSQL, PreflightReport, StudySummary
from preflight.estimate import estimate_scale
from preflight.lineage import build_lineage
from preflight.optimize import recommend
from preflight.parse.sql import parse_sql
from preflight.risk import assess_risk


def _query_shape(parsed) -> str:
    return f"{parsed.join_count} join(s), {len(parsed.cte_names)} CTE(s)" + (
        ", aggregated" if parsed.has_aggregation else "")


def run_preflight(sql: GeneratedSQL, catalog: Catalog,
                  connector: Optional[Connector] = None) -> PreflightReport:
    parsed = parse_sql(sql.query, dialect=sql.dialect)

    known = [t for t in parsed.base_tables if catalog.is_known(t)]
    known_ratio = (len(known) / len(parsed.base_tables)) if parsed.base_tables else 0.0

    # §7 live plan (optional, no execution)
    plan_rows = None
    query_plan = None
    if connector is not None:
        facts = connector.analyze(sql.query)
        query_plan = facts.to_query_plan()
        plan_rows = facts.total_estimated_rows

    # §1 summary
    domains = sorted({catalog.profile(t).category for t in parsed.base_tables})
    summary = StudySummary(
        execution_target=sql.target,
        tables=parsed.base_tables,
        domains=domains,
        query_shape=_query_shape(parsed),
    )

    # §2 lineage
    lineage = build_lineage(parsed, catalog)

    # §3 scale (+ live override) and runtime category
    scale, runtime = estimate_scale(parsed, catalog, known_ratio, plan_rows=plan_rows)

    # §4 risk
    risk_level, risk_reasons = assess_risk(parsed, catalog, scale)

    # §5 bottlenecks
    bottlenecks = find_bottlenecks(parsed, catalog)

    # §6 optimizations
    optimizations = recommend(parsed, catalog, scale)

    # §8 confidence
    confidence = score_confidence(known_ratio, has_plan=connector is not None)

    return PreflightReport(
        summary=summary,
        lineage=lineage,
        scale=scale,
        risk_level=risk_level,
        risk_reasons=risk_reasons,
        bottlenecks=bottlenecks,
        optimizations=optimizations,
        query_plan=query_plan,
        runtime_category=runtime,
        confidence=confidence,
    )
```

Append to `preflight/__init__.py`:
```python
from preflight.pipeline import run_preflight  # noqa: E402

__all__ = ["run_preflight", "__version__"]
```

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `python -m pytest -v`
Expected: PASS (all tests across all files)

- [ ] **Step 5: Commit**

```bash
git add preflight/pipeline.py preflight/__init__.py tests/test_pipeline.py
git commit -m "feat(preflight): pipeline orchestration (run_preflight)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Report rendering (text / markdown / json)

**Files:**
- Create: `preflight/report/__init__.py` (empty)
- Create: `preflight/report/render.py`
- Test: `tests/test_render.py`

Renders a `PreflightReport` to a human-readable text/markdown report and to JSON. Covers all 8 sections.

- [ ] **Step 1: Write the failing test**

`tests/test_render.py`:
```python
import json

from preflight.contracts import GeneratedSQL
from preflight.catalog.loader import load_catalog
from preflight.pipeline import run_preflight
from preflight.report.render import render_text, render_json
from fixtures.build_omop import load_query


def _report():
    sql = GeneratedSQL(query=load_query("crrt_flowsheet"), dialect="duckdb", target="omop")
    return run_preflight(sql, load_catalog("omop"))


def test_render_text_includes_all_sections():
    out = render_text(_report())
    for header in ["STUDY SUMMARY", "DATA LINEAGE", "SCALE", "RISK",
                   "BOTTLENECK", "OPTIMIZATION", "CONFIDENCE"]:
        assert header in out.upper()


def test_render_json_roundtrips():
    out = render_json(_report())
    data = json.loads(out)
    assert data["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert data["summary"]["execution_target"] == "omop"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.report'`

- [ ] **Step 3: Write the implementation**

`preflight/report/__init__.py`: (empty file)

`preflight/report/render.py`:
```python
"""Render a PreflightReport to text, markdown, or JSON."""
from __future__ import annotations

import json

from preflight.contracts import PreflightReport


def render_json(report: PreflightReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2)


def _fmt(n):
    return f"{n:,}" if isinstance(n, int) else "n/a"


def render_text(report: PreflightReport) -> str:
    s = report.summary
    lines = []
    lines.append("=== STUDY SUMMARY ===")
    lines.append(f"Execution Target: {s.execution_target}")
    lines.append(f"Tables: {', '.join(s.tables) or 'none'}")
    lines.append(f"Domains: {', '.join(s.domains) or 'none'}")
    lines.append(f"Query Shape: {s.query_shape}")

    lines.append("\n=== DATA LINEAGE ===")
    for n in report.lineage.nodes:
        rows = _fmt(n.est_rows) if n.est_rows is not None else "unknown"
        lines.append(f"  {n.table} [{n.category}, vol={n.volume}, ~{rows} rows]")
    for e in report.lineage.edges:
        lines.append(f"  {e.source} -> {e.target}  ({e.cardinality_transition})")
    if report.lineage.filters:
        lines.append(f"  filters: {', '.join(report.lineage.filters)}")

    sc = report.scale
    lines.append("\n=== SCALE ESTIMATION ===")
    lines.append(f"Patients: {_fmt(sc.patients)}")
    lines.append(f"Encounters: {_fmt(sc.encounters)}")
    lines.append(f"Events: {_fmt(sc.events)}")
    lines.append(f"Output Records: {_fmt(sc.output_records)}")
    lines.append(f"Runtime Category: {report.runtime_category.value}")
    lines.append(f"Scale Confidence: {sc.confidence.value}")

    lines.append("\n=== EXECUTION RISK ===")
    lines.append(f"Risk: {report.risk_level.value}")
    for r in report.risk_reasons:
        lines.append(f"  - {r}")

    lines.append("\n=== BOTTLENECK ANALYSIS ===")
    for b in report.bottlenecks:
        lines.append(f"  {b.component}: {b.contribution_pct}%  ({b.reason})")

    lines.append("\n=== OPTIMIZATION RECOMMENDATIONS ===")
    if not report.optimizations:
        lines.append("  (none — query is already lean)")
    for o in report.optimizations:
        lines.append(f"  * {o.action}")
        lines.append(f"      why: {o.rationale}")
        if o.expected_benefit:
            lines.append(f"      benefit: {o.expected_benefit}")

    if report.query_plan is not None:
        lines.append("\n=== QUERY PLAN (live) ===")
        qp = report.query_plan
        lines.append(f"Total Estimated Rows: {_fmt(qp.total_estimated_rows)}")
        for n in qp.nodes:
            bits = [n.op]
            if n.table:
                bits.append(f"table={n.table}")
            if n.join_type:
                bits.append(f"join={n.join_type}")
            if n.estimated_rows is not None:
                bits.append(f"~{_fmt(n.estimated_rows)} rows")
            lines.append("  " + " ".join(bits))

    lines.append("\n=== CONFIDENCE ===")
    lines.append(f"Overall Confidence: {report.confidence.value}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_render.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/report/ tests/test_render.py
git commit -m "feat(preflight): report rendering (text + json)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: CLI

**Files:**
- Create: `preflight/cli.py`
- Modify: `pyproject.toml` (add console script)
- Test: `tests/test_cli.py`

`preflight check <query.sql> --dialect duckdb --catalog omop [--target omop] [--format text|json] [--duckdb-fixture]`. The `--duckdb-fixture` flag attaches the synthetic OMOP DuckDB connector so §7 runs end-to-end with no external DB.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.cli'`

- [ ] **Step 3: Write the implementation**

`preflight/cli.py`:
```python
"""Command-line entry point for the Preflight analyzer."""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from preflight.catalog.loader import load_catalog
from preflight.contracts import GeneratedSQL
from preflight.pipeline import run_preflight
from preflight.report.render import render_json, render_text


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="preflight", description="SQL pre-execution analyzer")
    sub = parser.add_subparsers(dest="command", required=True)

    chk = sub.add_parser("check", help="Analyze a generated SQL file")
    chk.add_argument("sql_file", help="Path to a .sql file")
    chk.add_argument("--dialect", default="generic")
    chk.add_argument("--catalog", default="omop", help="Schema family catalog name")
    chk.add_argument("--target", default="generic", help="Execution-target label for the report")
    chk.add_argument("--format", choices=["text", "json"], default="text")
    chk.add_argument("--duckdb-fixture", action="store_true",
                     help="Attach the synthetic OMOP DuckDB connector for live plan analysis")

    args = parser.parse_args(argv)

    if args.command == "check":
        with open(args.sql_file, "r") as fh:
            query = fh.read()
        sql = GeneratedSQL(query=query, dialect=args.dialect, target=args.target)
        catalog = load_catalog(args.catalog)

        connector = None
        if args.duckdb_fixture:
            from fixtures.build_omop import build_omop_duckdb
            from preflight.connector.duckdb_connector import DuckDBConnector
            connector = DuckDBConnector(build_omop_duckdb())

        report = run_preflight(sql, catalog, connector=connector)
        out = render_json(report) if args.format == "json" else render_text(report)
        print(out)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
```

Add to `pyproject.toml` under `[project]`:
```toml
[project.scripts]
preflight = "preflight.cli:main"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/cli.py pyproject.toml tests/test_cli.py
git commit -m "feat(preflight): CLI (check command, text/json, fixture connector)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: Postgres connector ("any SQL DB" proof point)

**Files:**
- Create: `preflight/connector/postgres_connector.py`
- Modify: `requirements.txt` (add optional psycopg under a comment)
- Test: `tests/test_connector_postgres.py`

Postgres uses `EXPLAIN (FORMAT JSON)` which carries `Plan Rows` natively (no execution). The test parses a **captured JSON plan** (no live DB needed) to keep the suite hermetic; an opt-in live test runs only if `$PREFLIGHT_PG_DSN` is set.

- [ ] **Step 1: Write the failing test**

`tests/test_connector_postgres.py`:
```python
import os
import pytest

from preflight.connector.postgres_connector import parse_pg_plan, PostgresConnector

# A representative Postgres EXPLAIN (FORMAT JSON) output (estimates only, not executed).
PG_PLAN = [{
    "Plan": {
        "Node Type": "Hash Join", "Join Type": "Inner", "Plan Rows": 4200,
        "Plans": [
            {"Node Type": "Seq Scan", "Relation Name": "measurement",
             "Plan Rows": 5000, "Filter": "(measurement_concept_id = 3016723)"},
            {"Node Type": "Index Scan", "Relation Name": "person",
             "Index Name": "person_pkey", "Plan Rows": 100},
        ],
    }
}]


def test_parse_pg_plan_extracts_facts():
    facts = parse_pg_plan(PG_PLAN)
    assert facts.total_estimated_rows == 4200
    ops = {n.op for n in facts.nodes}
    assert "Hash Join" in ops and "Seq Scan" in ops
    seq = next(n for n in facts.nodes if n.op == "Seq Scan")
    assert seq.table == "measurement"
    assert seq.estimated_rows == 5000
    idx = next(n for n in facts.nodes if n.op == "Index Scan")
    assert idx.index_used is True


def test_seq_scan_on_large_table_emits_missing_index_hint():
    facts = parse_pg_plan(PG_PLAN)
    assert any("measurement" in h for h in facts.missing_index_hints)


@pytest.mark.skipif(not os.environ.get("PREFLIGHT_PG_DSN"),
                    reason="set PREFLIGHT_PG_DSN to run live Postgres test")
def test_live_postgres_explain():
    conn = PostgresConnector(os.environ["PREFLIGHT_PG_DSN"])
    facts = conn.analyze("SELECT 1")
    assert facts is not None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_connector_postgres.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.connector.postgres_connector'`

- [ ] **Step 3: Write the implementation**

`preflight/connector/postgres_connector.py`:
```python
"""Postgres live connector. EXPLAIN (FORMAT JSON) carries Plan Rows; no execution."""
from __future__ import annotations

from typing import Any, List

from preflight.connector.base import PlanFacts
from preflight.contracts import PlanNode

_SEQ_SCAN_HINT_THRESHOLD = 1000  # seq scan estimating more rows than this -> suggest index


def parse_pg_plan(plan_json: List[dict]) -> PlanFacts:
    root = plan_json[0]["Plan"]
    nodes: List[PlanNode] = []
    hints: List[str] = []

    def walk(node: dict):
        op = node.get("Node Type", "?")
        pn = PlanNode(
            op=op,
            table=node.get("Relation Name"),
            estimated_rows=node.get("Plan Rows"),
            scan_type=op if "Scan" in op else None,
            join_type=node.get("Join Type") if "Join" in op else None,
            index_used=("Index" in op) or ("Index Name" in node),
        )
        nodes.append(pn)
        if op == "Seq Scan" and (node.get("Plan Rows") or 0) > _SEQ_SCAN_HINT_THRESHOLD:
            rel = node.get("Relation Name", "?")
            filt = node.get("Filter", "")
            hints.append(f"Sequential scan on {rel}; consider an index{(' for ' + filt) if filt else ''}")
        for child in node.get("Plans", []) or []:
            walk(child)

    walk(root)
    return PlanFacts(nodes=nodes, total_estimated_rows=root.get("Plan Rows"),
                     missing_index_hints=hints)


class PostgresConnector:
    def __init__(self, dsn: str):
        self._dsn = dsn

    def analyze(self, sql: str) -> PlanFacts:
        import psycopg  # imported lazily; only needed for live use
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("EXPLAIN (FORMAT JSON) " + sql)  # NOT ANALYZE -> no execution
                plan_json = cur.fetchone()[0]
        return parse_pg_plan(plan_json)
```

Add to `requirements.txt` (commented — optional dependency):
```
# Optional, only for the Postgres live connector:
# psycopg[binary]>=3.1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_connector_postgres.py -v`
Expected: PASS (2 tests run, 1 skipped — the live test, unless `$PREFLIGHT_PG_DSN` is set)

- [ ] **Step 5: Commit**

```bash
git add preflight/connector/postgres_connector.py requirements.txt tests/test_connector_postgres.py
git commit -m "feat(preflight): Postgres connector (EXPLAIN JSON, hermetic + opt-in live)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Seed Epic + PCORnet catalogs & no-LLM guard test

**Files:**
- Create: `preflight/catalog/schemas/epic.yaml`
- Create: `preflight/catalog/schemas/pcornet.yaml`
- Create: `tests/test_no_llm_guard.py`
- Test: `tests/test_catalog_more.py`

Adds two more seed catalogs (proving "schema = a YAML file") and a guard test asserting the core package imports no LLM/network client.

- [ ] **Step 1: Write the failing tests**

`tests/test_catalog_more.py`:
```python
from preflight.catalog.loader import load_catalog


def test_epic_catalog_has_flowsheet_table():
    cat = load_catalog("epic")
    prof = cat.profile("IP_FLWSHT_MEAS")
    assert prof.volume == "huge"
    assert prof.risk == "very_high"


def test_pcornet_catalog_has_lab_result_cm():
    cat = load_catalog("pcornet")
    assert cat.is_known("LAB_RESULT_CM")
```

`tests/test_no_llm_guard.py`:
```python
import sys

import preflight  # noqa: F401  (import the whole core package)
import preflight.pipeline  # noqa: F401


def test_no_llm_or_http_client_imported():
    forbidden = {"openai", "anthropic", "requests", "httpx", "urllib3"}
    loaded = set(sys.modules)
    leaked = forbidden & loaded
    assert not leaked, f"core path must not import LLM/network clients, found: {leaked}"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_catalog_more.py tests/test_no_llm_guard.py -v`
Expected: `test_catalog_more` FAILs (`FileNotFoundError` for epic/pcornet). `test_no_llm_guard` should PASS already (no forbidden imports) — that is acceptable; it is a standing guard.

- [ ] **Step 3: Write the seed catalogs**

`preflight/catalog/schemas/epic.yaml`:
```yaml
schema: epic
tables:
  PAT_ENC_HSP:
    category: encounter
    volume: medium
    risk: low
    row_estimate: 7200000
  IP_FLWSHT_REC:
    category: flowsheet
    volume: large
    risk: high
    row_estimate: 80000000
  IP_FLWSHT_MEAS:
    category: clinical_event
    volume: huge
    risk: very_high
    row_estimate: 42000000000
  MAR_ADMIN_INFO:
    category: clinical_event
    volume: large
    risk: high
    row_estimate: 900000000
joins:
  PAT_ENC_HSP->IP_FLWSHT_REC: high
  IP_FLWSHT_REC->IP_FLWSHT_MEAS: high
columns:
  IP_FLWSHT_MEAS.FLO_MEAS_ID: 0.0002
```

`preflight/catalog/schemas/pcornet.yaml`:
```yaml
schema: pcornet
tables:
  DEMOGRAPHIC:
    category: demographics
    volume: medium
    risk: low
    row_estimate: 2000000
  ENCOUNTER:
    category: encounter
    volume: large
    risk: medium
    row_estimate: 30000000
  LAB_RESULT_CM:
    category: clinical_event
    volume: huge
    risk: very_high
    row_estimate: 1200000000
joins:
  DEMOGRAPHIC->ENCOUNTER: high
  ENCOUNTER->LAB_RESULT_CM: high
columns:
  LAB_RESULT_CM.LAB_LOINC: 0.0006
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_catalog_more.py tests/test_no_llm_guard.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add preflight/catalog/schemas/epic.yaml preflight/catalog/schemas/pcornet.yaml tests/test_catalog_more.py tests/test_no_llm_guard.py
git commit -m "feat(preflight): seed Epic + PCORnet catalogs; no-LLM guard test

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 17: Full-suite verification + README

**Files:**
- Create: `README.md`
- Test: (whole suite)

- [ ] **Step 1: Run the entire test suite**

Run: `. .venv/bin/activate && python -m pytest -v`
Expected: ALL tests pass (1 skipped: the live Postgres test).

- [ ] **Step 2: Run the CLI end-to-end against the fixture query**

Run:
```bash
. .venv/bin/activate
python -m preflight.cli check fixtures/queries/crrt_flowsheet.sql \
  --dialect duckdb --catalog omop --target omop --duckdb-fixture
```
Expected: a full text report printing all sections including `QUERY PLAN (live)`.

- [ ] **Step 3: Write the README**

`README.md`:
```markdown
# PSDL Workbench — Preflight Check (standalone)

Deterministic, zero-LLM SQL pre-execution analyzer. Given generated SQL + a metadata
catalog (+ optional read-only DB connector), it produces lineage, scale, risk,
bottleneck, optimization, live query-plan, and confidence sections — without executing
the query.

## Quickstart
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m pytest
python -m preflight.cli check fixtures/queries/crrt_flowsheet.sql \
  --dialect duckdb --catalog omop --target omop --duckdb-fixture
```

## Library API
```python
from preflight import run_preflight
from preflight.contracts import GeneratedSQL
from preflight.catalog.loader import load_catalog

report = run_preflight(
    GeneratedSQL(query=sql_text, dialect="duckdb", target="omop"),
    load_catalog("omop"),
)
print(report.risk_level, report.confidence)
```

## Design
See `docs/superpowers/specs/2026-05-29-preflight-check-design.md`.

## Adding a schema
Drop a YAML file in `preflight/catalog/schemas/<name>.yaml` (tables/joins/columns). No code change.

## Adding a SQL dialect connector
Implement the `Connector` protocol in `preflight/connector/base.py` (one `analyze(sql) -> PlanFacts`,
EXPLAIN-only, never executes).
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(preflight): README + quickstart

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review — Spec Coverage

- §1 Study Summary → Task 12 (`StudySummary` from parsed SQL) + render Task 13. ✓
- §2 Data Lineage → Task 4 + contracts Task 1. ✓
- §3 Scale Estimation → Task 5. ✓
- §4 Execution Risk → Task 6. ✓
- §5 Bottleneck Analysis → Task 7. ✓
- §6 Optimization Recommendations → Task 8. ✓
- §7 Query Plan Analysis → Tasks 10 (DuckDB), 15 (Postgres), wired in Task 12. ✓
- §8 Confidence Score → Task 9. ✓
- Runtime categories (FAST/MODERATE/HEAVY/EXTREME/UNKNOWN) → Task 5 `runtime_for_rows`. ✓
- Metadata catalog (data, not code) → Task 3 + seeds in Tasks 3/16. ✓
- Deterministic / zero-LLM → enforced by Task 16 guard test. ✓
- No query execution → EXPLAIN-only connectors (Tasks 10, 15). ✓
- Decoupled own-contracts → Task 1; no main-repo imports anywhere. ✓
- Library API + CLI → Tasks 12, 14. ✓
- Schema-agnostic + dialect-pluggable → generic fallback (Task 3) + Connector protocol (Task 10). ✓
- Synthetic OMOP DuckDB end-to-end incl. §7 → Tasks 11, 12, 14. ✓
- Deferred (regeneration, historical/ML, SQL Server, FastAPI/HTML, semantic input) → correctly absent. ✓

**Type consistency check:** `GeneratedSQL`, `PreflightReport`, `Lineage`, `ScaleEstimate`, `Confidence`, `RiskLevel`, `RuntimeCategory`, `PlanNode`, `QueryPlan`, `Bottleneck`, `Optimization`, `StudySummary` defined once in Task 1 and used with matching field names throughout. `ParsedSQL` (Task 2), `Catalog`/`TableProfile` (Task 3), `PlanFacts` (Task 10) consistent across consumers. `run_preflight(sql, catalog, connector=None)` signature stable across Tasks 12/14. `analyze(sql) -> PlanFacts` consistent across DuckDB (Task 10) and Postgres (Task 15). No naming drift found.
