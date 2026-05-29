# PSDL Workbench — Preflight Check (Standalone Module) — Design

*Date: 2026-05-29*
*Status: Approved for planning*

## 1. Purpose & Scope

Preflight Check is a **deterministic, pre-execution SQL analyzer**. It sits at the very
end of the PSDL Workbench pipeline — after PSDL has been approved and SQL has been
generated, immediately before execution — and answers, *without running the query*:

- What data will this query scan? (data lineage)
- How big will it get? (scale / cardinality estimation)
- Is it dangerous? (execution risk)
- What is the most expensive part? (bottleneck analysis)
- What could be cheaper? (optimization recommendations)
- When a read-only DB connection exists: what does the real query plan say? (§7)
- How much should we trust the above? (confidence score)

It is developed and tested **standalone** in `psdl-workbench-preflight/`, then merges
into the main backend later (intended path: `backend/app/services/preflight/`) with
zero risk to main.

### Hard design constraints

1. **Deterministic, zero-LLM.** Because Preflight runs right before execution, the
   entire engine is rule / catalog / query-plan based. No LLM is in the path. Nothing
   to hallucinate. This is enforced, not aspirational.
2. **SQL is the subject.** The module analyzes generated SQL. It does **not** re-derive,
   re-define, or duplicate PSDL semantics — PSDL produced an approved semantic plan
   upstream; that is not this module's concern.
3. **Evaluate-only.** v1 produces a structured evaluation and stops. Whether a finding
   should trigger SQL **regeneration** is a separate, later decision that lives in the
   workbench (and must itself stay minimal/no-LLM). Out of scope here. We emit the
   structured signal a future regenerate step could consume; we do not act on it.
4. **No query execution.** The live connector issues `EXPLAIN`-class statements only,
   under read-only credentials. It never runs the analyzed query.
5. **Decoupled.** The module never imports main-repo code. It defines its own
   contracts; a thin adapter maps the workbench's `SQLQuery` onto them at integration.

### Out of scope for v1

- SQL regeneration / acting on findings (deferred; emit signal only).
- Historical / ML-based cost estimation (deferred; leave a seam).
- SQL Server live connector (interface designed for it; implementation deferred).
- Any LLM involvement.
- FastAPI service and HTML report (library API + CLI only for now).
- Re-deriving PSDL semantics.

## 2. Architecture

Self-contained Python package with its own data contracts.

```
preflight/
  contracts.py      # GeneratedSQL, Catalog*, PreflightReport (+ the 8 sections), enums
  pipeline.py       # run_preflight() — orchestrates the stages
  parse/sql.py      # sqlglot-based: SQL string -> tables, joins, filters, CTEs, aggregations
  lineage.py        # §2 lineage graph + cardinality transitions
  catalog/
    loader.py       # load + validate catalog YAML; generic fallback profile
    schemas/        # seed catalogs: omop.yaml, epic.yaml, pcornet.yaml
  estimate.py       # §3 scale + cost engine (Approach B: catalog baseline, plan-refined)
  risk.py           # §4 risk scoring
  bottleneck.py     # §5 contribution ranking
  optimize.py       # §6 recommendations (structured)
  connector/
    base.py         # Connector interface: explain(sql) -> RawPlan; parse_plan(RawPlan) -> PlanFacts
    duckdb.py       # first connector — in-process, no server
    postgres.py     # "any SQL DB" proof point (opt-in integration test)
  confidence.py     # §8 confidence score
  report/
    model.py        # PreflightReport assembly
    render.py       # -> text / markdown / json
  cli.py            # `preflight check ...`
fixtures/
  omop/             # synthetic OMOP DuckDB + paired scenario/query.sql examples
tests/
```

### Core entry point

```python
def run_preflight(
    sql: GeneratedSQL,                 # the subject — required
    catalog: Catalog,                  # schema profiles (pure data)
    connector: Connector | None = None # optional live EXPLAIN source
) -> PreflightReport: ...
```

`GeneratedSQL = { query: str, dialect: str, target: str }` — a thin contract mirroring
the shape of main's `SQLQuery` so the integration adapter is trivial.

### Data flow

```
GeneratedSQL
  → parse/sql        (AST: tables, joins, filters, CTEs, aggregations)
  → lineage          (graph + per-edge cardinality transition, annotated via catalog)
  → estimate         (scale + cost; catalog heuristic baseline)
      → [if connector] pull EXPLAIN, refine row estimates with plan facts
  → risk             (LOW / MEDIUM / HIGH / CRITICAL + reasons)
  → bottleneck       (ranked % contribution to estimated cost)
  → optimize         (structured, actionable recommendations + expected benefit)
  → confidence       (LOW / MEDIUM / HIGH)
  → report           (assemble + render)
```

Every stage is a pure function of its inputs and is independently unit-testable. The
connector is the only impure/optional dependency and is fully mockable behind `base.py`.

## 3. The 8 PRD sections → `PreflightReport`

| Field | PRD § | Source |
|-------|-------|--------|
| `summary` | §1 | SQL-derived: execution target/dialect, tables & domains touched, query shape (joins, CTEs, aggregations) |
| `lineage` | §2 | SQL parse + catalog: nodes (tables), edges (joins/deps), filters, cardinality transitions |
| `scale` | §3 | Cost engine: patients/encounters/events/intermediate/output row estimates |
| `risk` | §4 | Rules over table volume, join depth, temporal scope, observation breadth, expected cardinality |
| `bottlenecks` | §5 | Ranked component contribution (% of estimated cost) |
| `optimization` | §6 | Structured recommendations + expected benefit |
| `query_plan` | §7 | `null` offline; populated from connector EXPLAIN (rows, join/scan types, index usage, missing-index hints, cost) |
| `confidence` | §8 | Function of catalog coverage + plan availability |
| `runtime_category` | (Runtime Categories) | FAST / MODERATE / HEAVY / EXTREME / UNKNOWN, from estimated cost |

The report is a structured, JSON-serializable object; `render.py` produces text /
markdown / JSON for the CLI.

## 4. Cost / estimation engine (Approach B)

Deterministic, two-layer:

1. **Catalog heuristic baseline (always):** propagate cardinality through the parsed
   query — base-table row counts (from catalog) × join fan-out × filter selectivity
   hints (from catalog), accumulating per-stage intermediate sizes. Explainable: every
   number traces to a catalog value or a documented heuristic.
2. **Plan refinement (when connector present):** override/blend the heuristic row
   estimates with the engine's real `EXPLAIN` estimated rows, and attach plan facts
   (scan/join types, index usage) for §7.

This directly drives confidence: **LOW** = sparse/missing catalog entries (generic
fallback used), **MEDIUM** = full catalog match, **HIGH** = full catalog + live plan.

Historical/ML estimation (PRD's 4th source) is deferred; `estimate.py` exposes a seam
where a historical adjuster could later plug in.

## 5. Metadata catalog (data, not code)

YAML per schema family, matching the PRD shape:

```yaml
tables:
  IP_FLWSHT_MEAS:
    category: flowsheet
    volume: huge          # or an explicit row_estimate
    risk: very_high
    row_estimate: 42000000
  PAT_ENC_HSP:
    category: encounter
    volume: medium
    risk: low
    row_estimate: 7200000
joins:                    # optional join profiles (fan-out hints)
  PAT_ENC_HSP->IP_FLWSHT_REC: { fanout: high }
columns:                  # optional per-column selectivity hints
  IP_FLWSHT_MEAS.FLO_MEAS_ID: { selectivity: 0.0001 }
```

Engine is **schema-agnostic**: tables not found in the catalog get a generic profile
(unknown volume/risk) and lower the confidence score. Adding a schema = adding a YAML
file, no code change. Seed catalogs shipped: `omop`, `epic`, `pcornet`.

## 6. Live connector (§7)

Interface in `connector/base.py`:

```python
class Connector(Protocol):
    def explain(self, sql: str) -> RawPlan: ...        # issues EXPLAIN-class stmt only
    def parse_plan(self, raw: RawPlan) -> PlanFacts: ...# rows, scan/join types, index use, cost
```

- **DuckDB connector first** — in-process, zero server. Used for the end-to-end test
  against a synthetic OMOP DuckDB fixture (`EXPLAIN` / `EXPLAIN (FORMAT JSON)` analogues).
- **Postgres connector second** — `EXPLAIN (FORMAT JSON)`; the "any SQL DB" proof point.
  Opt-in integration test (skipped without `$PREFLIGHT_PG_DSN`); otherwise mocked.
- SQL Server (`SHOWPLAN_XML`) deferred behind the same interface.

Read-only enforcement: connector only ever issues `EXPLAIN`-class statements; never
the analyzed query. (Postgres uses plain `EXPLAIN`, **not** `EXPLAIN ANALYZE`.)

## 7. Interfaces

- **Library API:** `run_preflight(...) -> PreflightReport` — the real integration surface.
- **CLI:** `preflight check <query.sql> --dialect duckdb [--catalog omop] [--connect <dsn|path>] [--format text|md|json]`.
  Fixtures provide paired scenario + `.sql` examples so the CLI runs end-to-end in isolation.

## 8. Testing strategy (fully isolated)

- Per-stage unit tests with hand-built contracts (no DB).
- **Synthetic OMOP DuckDB fixture** → end-to-end test of the whole pipeline **including
  §7** with the real DuckDB connector, no server.
- Golden-file tests on the rendered report for the OMOP fixture.
- Postgres connector: mockable interface + opt-in integration test (`$PREFLIGHT_PG_DSN`).
- A guard test asserting **no LLM/network dependency** is importable in the core path.

## 9. Dependencies

`sqlglot` (dialect-aware SQL parsing/lineage), `duckdb`, `pyyaml`, `pydantic` (v2, match
main). `psycopg`/`psycopg2` only for the optional Postgres connector. CLI via stdlib
`argparse`. Target **Python 3.9** for merge-compatibility with main.

## 10. Integration seam (later, in workbench session)

- A thin adapter maps the workbench's `SQLQuery` → `GeneratedSQL` and mounts
  `run_preflight` where SQL is generated.
- Preflight's structured `optimization` + live plan cardinalities are the **dry-run
  signal** a future regenerate step can consume. That feedback loop is explicitly **not**
  built here.
```
