# Epic EDW Support — Design (Phase 1)

**Date:** 2026-06-01
**Status:** Proposed — independently reviewed 2026-06-01 (findings applied); awaiting user sign-off
**Builds on:** `docs/superpowers/specs/2026-05-29-preflight-check-design.md`

## Goal

Make Preflight produce accurate reports for **Epic EDW** SQL, delivered in phases:

- **Phase 1 (this spec):** a **read-only catalog bootstrapper** that auto-generates catalog
  YAMLs by introspecting a database's *system catalogs*, plus T-SQL dialect support and a
  small wording fix. The bootstrapper is how catalogs get built (the user chose
  "bootstrapper first" over hand-writing). The analysis engine itself stays hermetic.
- **Phase 2 (separate, later spec):** a live, read-only **SQL Server connector** that
  pulls *estimated* execution plans (`SET SHOWPLAN_XML ON`, no execution). Optionally Oracle.

> **Decision (2026-06-01):** catalogs are produced by the auto-bootstrapper, not hand-written.
> Hand-tuned seed YAMLs are kept only as small, committed starters/test fixtures.

## Grounding (validated, not assumed)

Two real sample queries (UF Health OR-cases reports) were analyzed:

- They hit a **custom Clarity-derived T-SQL warehouse**, not textbook Clarity/Caboodle:
  `OR_ENCOUNTER_DTL`, `PATIENT_ENCOUNTER_DTL`, `ORDER_PROCEDURE_DTL`, `ALL_PATIENTS`,
  `ALL_PATIENT_SNAPSHOTS`, `ALL_PATIENT_IDENTITIES`, `ALL_PROVIDERS`, `ALL_OR_ROOMS`,
  `OR_CASE_KEY_XREF`, … Naming convention: `*_DTL` = detail/event (high volume),
  `ALL_*` = dimension/reference snapshot, `*_KEY_XREF` = crosswalk/bridge.
- **Dialect is T-SQL** (`dbo.` schemas, `ISNULL/CONVERT/CHARINDEX/DATEDIFF/DATEADD`,
  `{fn LEFT(...)}` ODBC escapes, single-quoted aliases, `'XXSTARTXX'` template tokens).
- **The parser already handles them** with `dialect="tsql"`: the 168 KB / 2,613-line
  query parsed to 21 base tables / 91 joins; the small one to 8 tables / 7 joins. The
  `generic` dialect fails on the `{fn}`/CAST syntax — so **tsql is required**. No parser
  rewrite is needed for Phase 1.

## Operational context: EDW refresh windows (offline-first)

Users typically cannot query Epic Chronicles directly — they read from the **Epic EDW
(Clarity/Caboodle), which is batch-refreshed by nightly ETL + backup**. During that
load/cleaning/backup window the warehouse is unavailable or mid-refresh, and query results
otherwise reflect "as of the last successful load." So **live EDW access is intermittent**,
and waiting for the refresh to finish is a real cost.

This is exactly why a **deterministic, offline, catalog-based** analyzer fits: the core
analysis path needs **no live connection**, so a query can be preflighted any time — even
while the EDW is mid-refresh or unavailable. Design consequences (folded into Phase 1):

- **Offline-first.** `run_preflight(sql, catalog)` with no connector is the primary mode;
  it depends only on the committed catalog YAML, never the EDW.
- **Bootstrapper is occasional, not per-query.** Catalog YAMLs are generated **once per
  refresh cadence** (run the bootstrapper during an availability window, post-load) and
  committed/cached. Day-to-day preflighting reads the static catalog — zero EDW access.
- **Freshness stamp.** The bootstrapper stamps each generated YAML with a top-level
  `stats_as_of` (a caller-supplied timestamp string — the runtime forbids wall-clock reads),
  and `load_catalog` exposes it so a report/`--show-catalog-age` can note how stale the
  estimates are.
- **Graceful degradation.** When a live connector (bootstrapper or Phase-2 plan) cannot reach
  the EDW (e.g. mid-load), it fails with a clear message and the caller falls back to the
  offline catalog estimate rather than blocking.

## Decisions (from brainstorming)

1. **Catalogs:** model the *real* EDW as `clarity.yaml` **and** add a textbook
   `caboodle.yaml` (dimensional `*Fact`/`*Dim`). Keep existing `epic.yaml` (Chronicles
   flowsheet names) untouched so current tests stay green.
2. **Dialect = T-SQL**, defaulted per-catalog so users don't have to remember it.
3. **Test fixture:** a **synthetic** Epic-shaped T-SQL query — do **not** commit the
   user's real SQL.

## Phase 1 components

### 1. Catalog bootstrapper (read-only system-catalog introspection → YAML)
New module `preflight/catalog/bootstrap.py` + CLI subcommand `preflight catalog-bootstrap`.
It connects read-only and queries **system catalogs only** — never the user's analytical
query, never patient-data tables — to emit a catalog-YAML **draft** for human review.

- **Introspector protocol** (`introspect() -> List[TableStat]`, where `TableStat =
  {schema, name, row_estimate}`; optional `column_ndistinct(table)` for selectivity).
  Backend implementations:
  - `PostgresIntrospector(dsn)` — `SELECT n.nspname, c.relname, c.reltuples::bigint
    FROM pg_class c JOIN pg_namespace n … WHERE c.relkind='r'` (estimate from stats, no scan).
  - `DuckDBIntrospector(con)` — `duckdb_tables()` / `information_schema.tables`.
  - `SQLServerIntrospector(dsn)` — `SELECT s.name, t.name, SUM(p.rows) FROM sys.tables t
    JOIN sys.partitions p ON t.object_id=p.object_id AND p.index_id IN (0,1) GROUP BY …`
    (lazy `pyodbc`/`pymssql` import, optional dep). This is the path that generates the real
    Epic `clarity.yaml`/`caboodle.yaml` — the user runs it in their Epic-connected env. Tested
    hermetically by feeding a captured rowset (same pattern as the Postgres EXPLAIN-plan test);
    the live run is opt-in.
- **Pure mapping functions** (deterministic, unit-testable, no I/O):
  - `category_for(name, heuristic)` — `heuristic="epic"`: `_DTL$`→`encounter` if name contains
    `ENCOUNTER` else `clinical_event`; `^ALL_`→`demographics` if `PATIENT` else `dimension`;
    `_KEY_XREF$`→`bridge`; `Fact$`→`encounter`/`clinical_event`; `Dim$`→`dimension`.
    `heuristic="omop"`: built-in CDM name→category map. `"generic"`: by volume.
  - `volume_for(row_estimate)` and `risk_for(category, volume)` — deterministic threshold tables
    (volume thresholds mirror `loader._VOLUME_ROWS`).
- **Emit:** `to_yaml(catalog_dict)` writes the `omop.yaml`-shaped YAML with a header comment
  `# AUTO-GENERATED draft — review category/risk before committing` and `default_dialect`.
- **CLI:** `preflight catalog-bootstrap --postgres-dsn DSN | --duckdb-path PATH |
  --sqlserver-dsn DSN  --schema-name clarity --heuristic epic [--out clarity.yaml]`.
  Round-trips: the emitted YAML loads back via `load_catalog`.

This executes read-only metadata queries (counts/stats), which is distinct from the original
"no query execution" rule (that rule is about the *analyzed* SQL). Privacy: no PHI — only table
names, row counts, and n_distinct stats. (See privacy audit; same posture.)

### 2. Catalog content & category vocabulary (what the bootstrapper emits / seeds encode)
Seed YAMLs (`clarity.yaml`, `caboodle.yaml`) — small hand-tuned **starters + test fixtures**,
since we can't reach the user's SQL Server from here; the bootstrapper regenerates/extends them
against the real EDW. Same shape as `omop.yaml` (`schema`, `tables{category,volume,risk,
row_estimate}`, `joins`, `columns`) plus optional top-level `default_dialect: tsql`.

**Categories reuse the existing engine vocabulary** (`demographics`, `encounter`,
`clinical_event`) for behavior, plus display-only labels (`dimension`, `reference`,
`bridge`) for non-event tables that intentionally trigger no rules. This means **no engine
change is needed** — the existing `encounter`/`clinical_event` rollups and rules fire on the
right Epic tables (resolves review M1/S2/S4: no `EVENT_LIKE_CATEGORIES`, no missed 4th
category check, no speculative category strings).

- **clarity.yaml** — explicit category per table:
  - `encounter`-grain → category `encounter` (populates `ScaleEstimate.encounters`):
    `PATIENT_ENCOUNTER_DTL`, `OR_ENCOUNTER_DTL`, `AN_ENCOUNTER_DTL` — `volume: large`, `risk: high`.
  - event/line-item grain → category `clinical_event` (populates `events`; triggers the
    unfiltered-scan risk + filter optimization): `ORDER_PROCEDURE_DTL`,
    `OR_CASE_PROCEDURE_DTL`, `OR_LOG_PROCEDURE_DTL`, `AN_BLOCK_DTL` — `volume: large/huge`,
    `risk: high/very_high`.
  - `demographics`: `ALL_PATIENTS` — `medium`, `low`.
  - `dimension`/`reference` (display-only, no rules): `ALL_PATIENT_SNAPSHOTS`,
    `ALL_PATIENT_IDENTITIES`, `ALL_PROVIDERS`, `ALL_PROVIDER_IDENTITIES`, `ALL_OR_ROOMS`,
    `ALL_OR_PROCEDURES`, `ALL_OR_ANESTHESIA_TYPES`, `ALL_SERVICE_CODES`,
    `ALL_HOSPITAL_ORGANIZATIONS` — `small/medium`, `low`.
  - `bridge` (`*_KEY_XREF`): `OR_CASE_KEY_XREF`, `ORDR_PROC_KEY_XREF`,
    `PATNT_ENCNTR_KEY_XREF` — `low/medium`.
  - representative `joins` and a few `columns` selectivities. Row estimates are illustrative
    defaults, refinable from live stats.
- **caboodle.yaml** — canonical dimensional tables, mapped onto the same vocabulary:
  `EncounterFact`, `SurgicalCaseFact` → `encounter` (huge, high); `DiagnosisEventFact`,
  `LabComponentResultFact` → `clinical_event` (huge, high/very_high); `PatientDim` →
  `demographics`; `DepartmentDim`, `ProviderDim`, `DateDim` → `dimension` (small/medium, low),
  with `*Fact->*Dim` joins.

### 3. Output wording (small, OMOP jargon → schema-neutral) — review S1
The risk/optimize messages currently say "clinical event table" and "concept filter" (OMOP
vocabulary), which read wrong for an Epic analyst. Genericize in `risk.py` and `optimize.py`:
"clinical event table" → "high-volume event table"; "concept filter" → "a selective filter
(e.g. a code or date predicate)". Behavior unchanged; benefits all schemas. This is the only
engine-code edit in Phase 1.

### 4. CLI configuration: fixed catalog location, `.env`, dialect default
Goal (user req): generated catalog YAMLs live in a **fixed location** and are found
**transparently**; a local **`.env`** supplies defaults so the CLI "just works" without
repeating flags. CLI-first — **no UI in this phase** (deferred until the CLI is complete and tested).

- **Catalog resolution (fixed location).** `load_catalog(name)` resolves a catalog file by
  searching, in order: (1) an explicit path/`--catalog-dir`, (2) the user catalog dir
  `$PREFLIGHT_CATALOG_DIR` (default `~/.preflight/catalogs/`), (3) the packaged
  `preflight/catalog/schemas/`. So `--catalog clarity` finds `clarity.yaml` whether it's a
  committed seed or a bootstrapper-generated file in the user dir. The bootstrapper's `--out`
  defaults to writing into `$PREFLIGHT_CATALOG_DIR` so freshly generated catalogs are picked
  up with no path juggling.
- **`.env` auto-load.** A tiny built-in `KEY=VALUE` reader (no new dependency) loads `.env`
  from the CWD (and `~/.preflight/.env`) at CLI start, into `os.environ` if not already set.
  Recognized keys: `PREFLIGHT_CATALOG_DIR`, `PREFLIGHT_CATALOG`, `PREFLIGHT_DIALECT`,
  `PREFLIGHT_PG_DSN`, `PREFLIGHT_SQLSERVER_DSN`, `PREFLIGHT_DUCKDB_PATH`. **Precedence:**
  explicit CLI flag > env var (incl. `.env`) > catalog `default_dialect` > built-in default.
- **Privacy bonus (audit F2):** putting the DSN in a **gitignored** `.env` keeps credentials
  off the command line / out of shell history. Add `.env` to `.gitignore`; the connector flags
  fall back to `PREFLIGHT_*_DSN` when their flag is omitted.
- **Dialect default.** `preflight/catalog/loader.py`: `Catalog.__init__` gains
  `default_dialect: str = "generic"`; `load_catalog` reads `data.get("default_dialect",
  "generic")` and exposes it as `.default_dialect` (review S3). `cli.py`: `--dialect` default
  becomes `None`; effective dialect = `args.dialect or $PREFLIGHT_DIALECT or
  catalog.default_dialect or "generic"`. Existing `tests/test_cli.py` pass `--dialect`
  explicitly, so this is safe (review N1).
- The **library API is unchanged** (`GeneratedSQL.dialect` explicit; `load_catalog` gains
  optional dir resolution). Only the CLI gains the `.env`/auto-resolution convenience.

### 5. Synthetic test fixture
`fixtures/queries/epic_or_cases.sql` — a small, hand-written T-SQL query using the same
patterns: a couple `*_DTL` joins to `ALL_*` dims and a `*_KEY_XREF`, a `dbo.` schema, a
single-quoted alias, a date filter, and — **required** — a 3-arg `CONVERT(varchar(N), col,
style)` (and/or `DATEADD/DATEDIFF`). Review M2: `{fn ...}` escapes alone parse fine under the
`generic` dialect, so the fixture must include `CONVERT`-style T-SQL syntax for the
"`generic` raises `PreflightParseError`" assertion to actually hold. Contains no patient data.

### 6. Known v1 limitation (documented)
Derived-table aliases from `UNION ALL`/subqueries (e.g. `Table__1308`, `OR_KEY_XREF`) are
not resolved into lineage join edges (they are subqueries, not base tables). No crash;
base tables and join counts are still correct. Out of scope for Phase 1.

## Testing

- `tests/test_catalog_epic.py` — load `clarity` and `caboodle`; assert key tables exist
  with expected `category`/`volume`/`risk` (e.g. `profile("OR_ENCOUNTER_DTL").category ==
  "encounter"`, `profile("ORDER_PROCEDURE_DTL").category == "clinical_event"` — review N5);
  assert `default_dialect == "tsql"`.
- `tests/test_parse_tsql_epic.py` — parse the synthetic Epic query with `dialect="tsql"`;
  assert expected base tables and join count; assert `generic` raises `PreflightParseError`.
  The fixture must contain `CONVERT(varchar(N), col, style)` for this to hold (review M2).
- `tests/test_pipeline_epic.py` — run the synthetic query through `run_preflight` with the
  `clarity` catalog; assert risk is HIGH/CRITICAL (event-grain `*_DTL` + multi-join), the top
  bottleneck is the highest-volume event table, `scale.encounters is not None` (review M1),
  and optimizations are non-empty.
- `tests/test_cli.py` — `--catalog clarity` with no `--dialect` parses as tsql; a `.env` in
  CWD supplies `PREFLIGHT_DIALECT`/`PREFLIGHT_CATALOG`/`PREFLIGHT_PG_DSN` and the CLI honors
  them (monkeypatch CWD + env); explicit flags override `.env`.
- `tests/test_bootstrap.py` — pure mapping units (`category_for`/`volume_for`/`risk_for` on
  Epic-named inputs); `bootstrap_catalog` over a captured introspection rowset emits YAML with
  expected categories; round-trip: emitted YAML → `load_catalog` succeeds. Plus a real
  `DuckDBIntrospector` test against the synthetic fixture, and a gated live `PostgresIntrospector`
  test (`$PREFLIGHT_PG_DSN`).
- `tests/test_catalog_resolution.py` — `load_catalog` prefers `$PREFLIGHT_CATALOG_DIR` over the
  packaged dir; falls back to packaged when absent (use `tmp_path`).
- Existing `tests/test_no_llm_guard.py` and all current tests must stay green.

## Out of scope (Phase 1)

**Any UI / web frontend (explicitly deferred — CLI must be complete and tested first).** Also:
live SQL Server / Oracle *plan* connector (Phase 2; the bootstrapper's SQL Server *introspector*
IS in Phase 1 but its live run is opt-in/user-run), derived-table lineage resolution,
naming-convention auto-inference for unseeded tables, real row-count calibration beyond what the
bootstrapper reads.

## Related: privacy follow-up (separate from this feature)

The 2026-06-01 privacy audit (`docs/privacy-and-data-handling-audit.md`) flagged that the
Postgres connector copied plan `Filter` predicate **literals** into `missing_index_hints`
(F1, Medium). **F1 is now fixed** (commit `881ef5e`: `_redact_literals` strips literal values
to `?`). The same redaction must be applied to any predicate text the Phase-2 SQL Server
connector surfaces — carry this forward into the Phase-2 spec.
