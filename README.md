# Preflight Check

**Know the cost of a study before you run it.**

Preflight is a **deterministic, zero-LLM SQL pre-execution analyzer**. Give it a SQL query plus
a lightweight metadata catalog (and, optionally, a read-only database connection) and it produces
a structured report — data lineage, scale/cost estimate, execution risk, bottlenecks, optimization
recommendations, live query-plan facts, and a confidence score — **without ever executing the
query**.

It was built for clinical research data warehouses (OMOP, Epic Clarity/Caboodle, PCORnet), where
a single generated query can scan billions of rows, but it works on any SQL.

## Why

- **Deterministic & zero-LLM.** Every result comes from rules, catalog metadata, and (optionally)
  the database's own query planner — no model in the path, nothing to hallucinate. Enforced by a
  guard test.
- **Never executes your query.** Live analysis uses only `EXPLAIN`-class statements
  (Postgres `EXPLAIN (FORMAT JSON)`, DuckDB `EXPLAIN`, SQL Server `SET SHOWPLAN_XML ON`) — the
  *estimated* plan, never the actual run.
- **Offline-first.** The core analysis needs no database connection at all — just the SQL and a
  catalog YAML. Useful when the warehouse is mid-ETL or otherwise unavailable.
- **Schema-agnostic & dialect-pluggable.** Schemas are plain YAML data; SQL dialects come from
  sqlglot; connectors implement one small interface.

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # sqlglot, duckdb, pydantic, pyyaml
# optional live connectors:
#   pip install "psycopg[binary]"         # Postgres
#   pip install pyodbc                    # SQL Server (needs an ODBC driver)
```

## Quickstart

```bash
# Offline (catalog-only) — no database needed:
python -m preflight.cli check my_query.sql --catalog omop --dialect duckdb

# With a live estimated plan, using the bundled synthetic OMOP DuckDB (no server):
python -m preflight.cli check fixtures/queries/crrt_flowsheet.sql \
  --catalog omop --target omop --duckdb-fixture

# JSON output:
python -m preflight.cli check my_query.sql --catalog omop --format json
```

## Triage TUI (Conduct of Operations)

An interactive, color terminal view for vetting queries and making a fast go / no-go call:

```bash
# Single query — a triage panel with a GO / REVIEW / BLOCK verdict:
python -m preflight.cli tui my_query.sql --catalog clarity

# A whole folder — a worklist of all *.sql, sorted worst-risk-first; pick one to drill in:
python -m preflight.cli tui ./queries/ --catalog omop --dialect duckdb

# Attach a live plan (any read-only connector) and add --no-input for scripted/CI use:
python -m preflight.cli tui report.sql --catalog clarity --sqlserver-dsn "..." --no-input
```

Verdicts map from risk: `LOW → GO`, `MEDIUM → GO (caution)`, `HIGH → REVIEW`, `CRITICAL → BLOCK`
(an `EXTREME` runtime escalates a borderline case). Requires `rich` (in `requirements.txt`).

## What's in the report

| Section | What it tells you |
|---|---|
| **Study Summary** | execution target, tables touched, domains, query shape |
| **Data Lineage** | tables, join edges, cardinality transitions (fan-out), filters |
| **Scale Estimation** | patients / encounters / events / output rows; runtime category (FAST…EXTREME) |
| **Execution Risk** | LOW · MEDIUM · HIGH · CRITICAL, with reasons |
| **Bottleneck Analysis** | tables ranked by estimated row contribution |
| **Optimization Recommendations** | structured, actionable suggestions |
| **Query Plan (live)** | when a connector is attached: scan/seek/join ops, est. rows, index usage, missing-index hints |
| **Confidence** | LOW (sparse catalog) · MEDIUM (full catalog) · HIGH (catalog + live plan) |

## Library API

```python
from preflight import run_preflight
from preflight.contracts import GeneratedSQL
from preflight.catalog.loader import load_catalog

report = run_preflight(
    GeneratedSQL(query=sql_text, dialect="duckdb", target="omop"),
    load_catalog("omop"),
    connector=None,   # or a read-only connector (see below)
)
print(report.risk_level, report.runtime_category, report.confidence)
for b in report.bottlenecks:
    print(b.component, b.contribution_pct)
```

Render a report with `preflight.report.render.render_text(report)` / `render_json(report)`.

## Live database connectors (all read-only, no execution)

| Database | How | CLI flag |
|---|---|---|
| DuckDB | in-process `EXPLAIN` | `--duckdb-path file.duckdb` (or `--duckdb-fixture` for the synthetic OMOP demo) |
| Postgres | `EXPLAIN (FORMAT JSON)` | `--postgres-dsn "postgresql://user@host/db"` |
| SQL Server | `SET SHOWPLAN_XML ON` (estimated plan) | `--sqlserver-dsn "Driver={ODBC Driver 18 for SQL Server};Server=...;Database=...;UID=...;PWD=..."` |

```bash
preflight check report.sql --catalog clarity \
  --sqlserver-dsn "Driver={ODBC Driver 18 for SQL Server};Server=...;Database=Clarity;UID=...;PWD=..."
```

Connectors only issue plan/EXPLAIN statements — they never run the analyzed query and never read
row data.

## Schemas & catalogs

Bundled seed catalogs: **`omop`**, **`epic`**, **`clarity`** (Epic Clarity-derived EDW),
**`caboodle`** (Epic dimensional), **`pcornet`**. A catalog is just a YAML file describing tables
(`category`, `volume`, `risk`, `row_estimate`), `joins`, and column `selectivity`.

### Generate a catalog from your own database (read-only)

The **bootstrapper** introspects your database's *system catalogs* (table list + row-count
estimates — no data, no query execution) and writes a catalog YAML draft:

```bash
# DuckDB / Postgres now; SQL Server (Epic) needs `pip install pyodbc`
preflight catalog-bootstrap \
  --sqlserver-dsn "Driver={ODBC Driver 18 for SQL Server};Server=...;Database=Clarity;..." \
  --schema-name clarity --heuristic epic --stats-as-of 2026-06-01
# writes ~/.preflight/catalogs/clarity.yaml (review the categories/risk before relying on it)
```

Catalogs in `$PREFLIGHT_CATALOG_DIR` (default `~/.preflight/catalogs/`) override the bundled seeds
and are found automatically by name. Run the bootstrapper once per warehouse refresh; day-to-day
`check` runs fully offline against the cached catalog.

### Config via `.env`

Put defaults in a (gitignored) `.env` so you don't repeat flags or expose credentials on the
command line:

```
PREFLIGHT_CATALOG=clarity
PREFLIGHT_DIALECT=tsql
PREFLIGHT_CATALOG_DIR=~/.preflight/catalogs
PREFLIGHT_SQLSERVER_DSN=Driver={ODBC Driver 18 for SQL Server};Server=...;Database=Clarity;...
```

Resolution precedence: CLI flag → environment / `.env` → catalog `default_dialect` → built-in default.

## Extending

- **Add a schema:** drop a YAML file in `preflight/catalog/schemas/<name>.yaml` (or
  `$PREFLIGHT_CATALOG_DIR`). No code change.
- **Add a connector:** implement the `Connector` protocol in `preflight/connector/base.py` —
  a single `analyze(sql) -> PlanFacts`, EXPLAIN-class only, never executing the query.

## Privacy & safety

- No query execution; connectors use estimated-plan statements only.
- No LLM and no network clients on the core path (guarded by a test).
- Plan predicate **literals are redacted** before they appear in any report (so values that could
  be identifiers never leak into output).
- Credentials belong in `.env`/environment, not on the command line.
- See `docs/privacy-and-data-handling-audit.md` for the full data-handling audit.

## Testing

```bash
. .venv/bin/activate && python -m pytest
```

Live database tests are skipped unless `PREFLIGHT_PG_DSN` / `PREFLIGHT_SQLSERVER_DSN` are set.

## Project layout

```
preflight/            core package (parser, catalog, lineage, estimate, risk, …)
  catalog/            YAML schema catalogs + loader + bootstrapper
  connector/          read-only DuckDB / Postgres / SQL Server connectors
  report/             text + JSON renderers
fixtures/             synthetic OMOP DuckDB + sample queries (no real data)
docs/                 design specs, plans, and the privacy audit
tests/                pytest suite
```

## Context

Preflight is a deferred feature of **PSDL Workbench**, developed standalone here so it can be used
and tested on its own. Design specs and implementation plans live under `docs/superpowers/`.
