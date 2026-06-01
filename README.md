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
