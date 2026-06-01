# Preflight — Privacy & Data-Handling Audit

**Date:** 2026-06-01
**Scope:** `preflight/` package (parser, catalog, lineage, estimate, risk, bottleneck,
optimize, confidence, pipeline, report, CLI, connectors) and `fixtures/`.
**Method:** code trace + empirical probes (pushed a query carrying a fake MRN literal
through the pipeline and inspected rendered text/JSON; exercised the Postgres plan parser).

## Executive summary

**Overall risk: LOW–MEDIUM.** The tool's core privacy promises hold: it is
deterministic and zero-LLM (enforced by a guard test), it never executes the analyzed
query (EXPLAIN-class statements only), it persists nothing to disk, and it makes no
third-party network calls. The **offline analysis path does not leak literal values** —
it captures column *names*, never the WHERE/JOIN literal *values* (verified empirically:
a query filtering on `person_id = 999000123` produced a report containing neither the MRN
nor the concept-id literal).

There is **one concrete data-leakage vector worth fixing**: the Postgres connector copies
the execution plan's `Filter` predicate **string** — literal values included — into
`missing_index_hints`, which are rendered in the §7 text report and serialized in JSON.
Plus two hygiene items (DSN-on-command-line; parse-error messages may echo SQL fragments).
None are catastrophic, and none affect the catalog-only (no-connector) path.

## Data-flow overview

```
SQL text ──> parse_sql (sqlglot)                  [in-process, no I/O]
                │  extracts: table NAMES, column NAMES, join graph, agg flag
                ▼
          catalog (static YAML)  ──> lineage / estimate / risk / bottleneck / optimize
                │                       (all operate on names + catalog stats only)
                ▼
          PreflightReport ──> render_text / render_json ──> stdout
                ▲
                │ (optional)
          Connector.analyze(sql)  ── EXPLAIN-only ──>  local/remote DB
            DuckDB: text EXPLAIN (read_only)        Postgres: EXPLAIN (FORMAT JSON)
```

Sensitive material enters only as (a) the SQL text and (b) — if a connector is attached —
whatever the DB returns in the *plan*. EXPLAIN returns plan estimates, not result rows.

## Findings

### F1 — Postgres plan `Filter` literals leak into the report — **MEDIUM**
`preflight/connector/postgres_connector.py` (`walk()`): for a `Seq Scan` it does
`hints.append(f"Sequential scan on {rel}; consider an index for {filt}")` where `filt`
is the raw plan `Filter` string. Postgres `Filter` strings embed **literal values**.
These hints flow into `QueryPlan.missing_index_hints` → rendered in §7 text (after the
render fix) and always present in `render_json`.

- **Evidence (empirical):** parsing a plan with `"Filter":"(person_id = 999000123)"`
  yielded hint `"Sequential scan on measurement; consider an index for (person_id = 999000123)"`.
- **Leak scenario:** a query with `WHERE pat_mrn = '1234567'` or `dob = '1950-02-03'`
  produces a report that embeds the MRN/DOB in plaintext — and JSON reports are exactly
  what a caller would log or persist.
- **Mitigation:** redact the RHS of predicates before storing — keep the column
  reference, drop the literal (e.g. `"…consider an index on measurement.person_id"`),
  or strip everything after the operator. Do the same for any DuckDB filter text if that
  path is later extended.

### F2 — Postgres DSN (with password) passed on the command line — **MEDIUM**
`preflight/cli.py` exposes `--postgres-dsn DSN`. A DSN typically embeds a password; CLI
args are visible in shell history and to `ps`/other local users. The DSN is also stored on
`PostgresConnector._dsn`. It is **not** rendered into the report (good), but the
command-line exposure is real.

- **Mitigation:** prefer an env var (the tests already use `PREFLIGHT_PG_DSN`); add a
  `--postgres-dsn-env` option or read `PREFLIGHT_PG_DSN` when `--postgres-dsn` is omitted,
  and document "do not put credentials on the command line." Point connectors at a
  read-only replica/role.

### F3 — Parse-error messages may echo SQL fragments — **LOW**
`preflight/parse/sql.py`: `raise PreflightParseError(f"Could not parse SQL: {exc}")`.
sqlglot errors include the offending token and line/col, which can be a literal value. The
exception is local (not transmitted), but if a caller logs it, a fragment of PHI-bearing
SQL could land in logs.

- **Mitigation:** document that parse errors may contain SQL fragments; optionally
  truncate the wrapped message or report only line/col.

### F4 — SQL text is sent to the (possibly remote) target DB — **LOW / informational**
When a connector is attached, the full SQL (which may contain PHI literals) is sent to the
database to be EXPLAINed. This is the legitimate target system, not a third party, and only
the *plan* comes back (no result rows — EXPLAIN, never EXPLAIN ANALYZE). There is **no
third-party / LLM / telemetry egress**: the `tests/test_no_llm_guard.py` guard asserts no
`openai/anthropic/requests/httpx/urllib3` is imported on the core path, and the only network
clients are `duckdb` (in-process) and lazily-imported `psycopg` (to the user's own DB).

- **Mitigation:** none required; note in deployment docs that the connector transmits the
  SQL to the configured DB.

### F5 — No execution, no persistence (positive control) — **INFORMATIONAL**
Verified: both connectors use EXPLAIN-class statements only (`EXPLAIN ` / `EXPLAIN (FORMAT
JSON) ` — never `EXPLAIN ANALYZE`); the CLI opens local DuckDB files with `read_only=True`;
the pipeline writes nothing to disk; reports go to stdout; the synthetic OMOP fixture
(`fixtures/build_omop.py`) generates only `random()`-based synthetic data and is test-only.

### F6 — The report itself discloses schema (and, via F1, possibly literals) — **LOW**
Even with the offline path, the report enumerates real table and column names (schema
disclosure). With a Postgres connector, F1 can add literals. Treat the rendered report —
especially JSON — as sensitive output.

- **Mitigation:** deployment guidance below.

## What the tool does well for privacy

- **Deterministic, zero-LLM**, enforced by an import-guard test — no prompt/data ever
  leaves for a model.
- **No query execution** — EXPLAIN-only connectors; DuckDB files opened read-only.
- **Offline path captures names, not values** — confirmed no literal-value leakage.
- **No raw SQL stored** in `PreflightReport`; **no disk persistence / caching / log files**.
- **Lazy, minimal dependencies** — `psycopg` only imported when actually connecting.

## Residual risks / deployment guidance

1. **Fix F1** before relying on the Postgres live path with real PHI — redact predicate
   literals from `missing_index_hints`.
2. **Credentials:** pass the Postgres DSN via `PREFLIGHT_PG_DSN`, not `--postgres-dsn`; use
   a read-only role/replica.
3. **Treat reports as sensitive:** JSON/text reports contain real schema and (with a PG
   connector) possibly predicate literals. Apply the same handling as other clinical
   metadata; avoid pasting reports into third-party tools.
4. **Logging:** if you log exceptions, be aware parse errors (F3) may contain SQL fragments.
5. The analyzed SQL is transmitted to the target DB when a connector is attached (F4) —
   ensure that DB is in-scope/authorized for the data.
