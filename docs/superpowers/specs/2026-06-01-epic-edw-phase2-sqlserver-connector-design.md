# Epic EDW Support — Phase 2: Live SQL Server Plan Connector (Design)

**Date:** 2026-06-01
**Status:** Proposed (awaiting user sign-off)
**Builds on:** `docs/superpowers/specs/2026-06-01-epic-edw-support-design.md` (Phase 1)

## Goal

Add a live, **read-only** SQL Server connector so Preflight can enrich its report with the
real **estimated** query plan from an Epic EDW (Clarity/Caboodle on SQL Server) — **without
executing the query**. This plugs into the existing pipeline: `run_preflight(sql, catalog,
connector=...)` already accepts anything implementing `analyze(sql) -> PlanFacts`, so no
engine change is needed.

## Decisions (from brainstorming)

1. **SQL Server only** (Caboodle is always SQL Server; modern Clarity usually is). Oracle deferred.
2. **Driver: pyodbc** (matches the Phase-1 `SQLServerIntrospector`; standard in Epic shops).
3. **Hermetic-first testing:** the parser is tested against a **captured `SHOWPLAN_XML`** sample
   (we cannot reach the user's SQL Server from CI). A live test is **opt-in**, gated on
   `$PREFLIGHT_SQLSERVER_DSN`, run by the user in their environment.

## How the no-execution guarantee holds

`SET SHOWPLAN_XML ON` makes SQL Server **return the estimated plan as XML instead of running
the statement**. The connector issues `SET SHOWPLAN_XML ON`, submits the query (only the plan
comes back — no result rows, no side effects), then `SET SHOWPLAN_XML OFF`. This is the
SQL Server analogue of Postgres `EXPLAIN (FORMAT JSON)` / DuckDB `EXPLAIN` — estimated, not
actual; never `SET STATISTICS`/`ACTUAL` plans.

## Components

### 1. Shared literal redaction (DRY refactor)
Phase 1 added `_redact_literals` inside `postgres_connector.py` (audit F1). Move it to a shared
module `preflight/connector/redact.py` as `redact_literals(predicate: str) -> str` and have
**both** connectors import it. SQL Server `SHOWPLAN_XML` predicates (`ScalarString` attributes)
embed literal values that can be PHI, so the same redaction must apply. Existing Postgres tests
keep passing (re-point the import).

### 2. `preflight/connector/sqlserver_connector.py`
- `parse_showplan_xml(xml_text: str) -> PlanFacts` — **pure**, hermetically testable. Parses the
  `SHOWPLAN_XML` (namespace `http://schemas.microsoft.com/sqlserver/2004/07/showplan`) by
  **local tag name** (namespace-agnostic) and produces `PlanFacts`:
  - One `PlanNode` per `<RelOp>` (document order). Fields:
    - `op` = `PhysicalOp` (e.g. "Index Seek", "Hash Match", "Clustered Index Scan").
    - `estimated_rows` = `round(float(EstimateRows))`.
    - `table` = nearest `<Object Table="[db].[dbo].[NAME]">` within the RelOp's **own** operator
      (not nested child RelOps); brackets/schema stripped to bare `NAME`.
    - `scan_type` = `PhysicalOp` when it contains "Scan".
    - `join_type` = `LogicalOp` when `PhysicalOp` is a join op ("Hash Match", "Nested Loops",
      "Merge Join") — e.g. "Inner Join".
    - `index_used` = `True` when `PhysicalOp` contains "Seek" or "Index".
  - `total_estimated_rows` = the **root** RelOp's `EstimateRows` (the direct `<RelOp>` child of
    `<QueryPlan>`).
  - `missing_index_hints`:
    - From SQL Server's own `<MissingIndexes>` element: one hint per `<MissingIndex>` —
      `"Missing index on <Table> (<equality/inequality cols>)"` (column **names** only — no
      literals, safe).
    - Plus, for a `*Scan` RelOp carrying a `<Predicate>` whose `EstimateRows` exceeds a
      threshold: `"Scan on <table>; predicate <redacted>"` — the `ScalarString` is passed through
      `redact_literals` (F1) so values become `?`.
- `class SQLServerConnector` with `__init__(self, dsn)` and `analyze(self, sql) -> PlanFacts`:
  lazily `import pyodbc`; open a connection; `cur.execute("SET SHOWPLAN_XML ON")`;
  `cur.execute(sql)`; read the single-column plan XML from `fetchone()`; `SET SHOWPLAN_XML OFF`;
  return `parse_showplan_xml(xml)`. Matches the `Connector` protocol.

### 3. CLI wiring
Add `--sqlserver-dsn` to the `check` subcommand's mutually-exclusive connector group, and resolve
it in `_build_connector` (flag → `$PREFLIGHT_SQLSERVER_DSN`). So:
`preflight check report.sql --catalog clarity --sqlserver-dsn "<odbc dsn>"` runs the live plan;
with the DSN in `.env` it needs no flag. (`pyodbc` already noted optional in `requirements.txt`.)

### 4. No pipeline/contract changes
`PlanFacts`/`PlanNode`/`QueryPlan` already model everything above. The report's §7 renderer
(already shows op/table/join/rows + index usage + missing-index hints) covers the new connector.

## Testing

- `tests/test_connector_sqlserver.py` (hermetic):
  - A captured representative `SHOWPLAN_XML` constant containing a `Hash Match (Inner Join)`
    root, an `Index Seek` (index_used), a `Clustered Index Scan` on `ORDER_PROCEDURE_DTL` with a
    `Predicate ScalarString="[ORDER_PROCEDURE_DTL].[PROC_CD]='12345'"`, and a `<MissingIndexes>`
    suggesting an index on `ORDER_PROCEDURE_DTL (PROC_CD)`.
  - `test_parse_showplan_extracts_facts`: `total_estimated_rows` == root EstimateRows; ops include
    the three physical ops; a node has `join_type`; a node has `scan_type`; the Index Seek node
    has `index_used is True`; tables `ORDER_PROCEDURE_DTL` and `ALL_PATIENTS` resolved (brackets
    stripped).
  - `test_missing_index_hint_from_showplan`: a hint mentions `ORDER_PROCEDURE_DTL` and `PROC_CD`.
  - `test_scan_predicate_literal_is_redacted`: `'12345'` does **not** appear in any hint; `PROC_CD`
    does (F1 carried forward).
  - `test_sqlserver_connector_constructs_without_driver`: `SQLServerConnector("dsn")` constructs.
  - Gated live test `@skipif(not $PREFLIGHT_SQLSERVER_DSN)`: `analyze("SELECT 1")` returns PlanFacts.
- `tests/test_connector_postgres.py`: still green after the `redact_literals` import move (add an
  import-path assertion or just rely on existing redaction tests).
- `tests/test_cli.py` / `test_cli_config.py`: add a case that `--sqlserver-dsn` is accepted on
  `check` and is mutually exclusive with the other connector flags (argparse `SystemExit` when two
  are given). The live behavior is exercised only when the DSN env is set.
- Full suite stays green; new live test skipped by default.

## Out of scope (Phase 2)
Oracle connector; actual-execution plans / `SET STATISTICS`; parsing operator cost/IO details
beyond rows; surfacing the plan tree shape (parent/child) — nodes are a flat document-order list,
consistent with the DuckDB/Postgres connectors.

## Privacy
Read-only estimated plan; no result rows. `SHOWPLAN_XML` predicate literals are redacted via the
shared `redact_literals` (audit F1). The DSN (may carry credentials) is taken from
`--sqlserver-dsn` or `$PREFLIGHT_SQLSERVER_DSN` (prefer `.env`, gitignored — audit F2) and is
never rendered or logged.
