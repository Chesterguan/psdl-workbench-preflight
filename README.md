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
