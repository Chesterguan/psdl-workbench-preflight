# Epic EDW Support — Design (Phase 1)

**Date:** 2026-06-01
**Status:** Proposed (awaiting user review)
**Builds on:** `docs/superpowers/specs/2026-05-29-preflight-check-design.md`

## Goal

Make Preflight produce accurate reports for **Epic EDW** SQL, delivered in phases:

- **Phase 1 (this spec):** offline support — seed Epic catalogs + correct dialect
  parsing + a small engine generalization so the existing risk/scale/optimize rules fire
  on Epic event-grain tables. Fully hermetic (no DB connection required).
- **Phase 2 (separate, later spec):** a live, read-only **SQL Server connector** that
  pulls *estimated* execution plans (`SET SHOWPLAN_XML ON`, no execution). Optionally Oracle.

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

## Decisions (from brainstorming)

1. **Catalogs:** model the *real* EDW as `clarity.yaml` **and** add a textbook
   `caboodle.yaml` (dimensional `*Fact`/`*Dim`). Keep existing `epic.yaml` (Chronicles
   flowsheet names) untouched so current tests stay green.
2. **Dialect = T-SQL**, defaulted per-catalog so users don't have to remember it.
3. **Test fixture:** a **synthetic** Epic-shaped T-SQL query — do **not** commit the
   user's real SQL.

## Phase 1 components

### 1. Catalog data (the bulk)
`preflight/catalog/schemas/clarity.yaml` and `caboodle.yaml`, same shape as `omop.yaml`
(`schema`, `tables{category,volume,risk,row_estimate}`, `joins`, `columns`), plus one new
optional top-level key `default_dialect: tsql`.

- **clarity.yaml** seeds the observed tables with institution-scale estimates:
  - detail/event (`*_DTL`): `OR_ENCOUNTER_DTL`, `PATIENT_ENCOUNTER_DTL` (encounter),
    `ORDER_PROCEDURE_DTL`, `OR_CASE_PROCEDURE_DTL`, `OR_LOG_PROCEDURE_DTL`,
    `AN_ENCOUNTER_DTL`, `AN_BLOCK_DTL` — categories `encounter` or `clinical_event`/`detail`,
    `volume: large/huge`, `risk: high/very_high`.
  - dimension/reference (`ALL_*`): `ALL_PATIENTS` (demographics), `ALL_PATIENT_SNAPSHOTS`,
    `ALL_PATIENT_IDENTITIES`, `ALL_PROVIDERS`, `ALL_PROVIDER_IDENTITIES`, `ALL_OR_ROOMS`,
    `ALL_OR_PROCEDURES`, `ALL_OR_ANESTHESIA_TYPES`, `ALL_SERVICE_CODES`,
    `ALL_HOSPITAL_ORGANIZATIONS` — `dimension`/`reference`, `volume: small/medium`, `risk: low`.
  - crosswalk (`*_KEY_XREF`): `OR_CASE_KEY_XREF`, `ORDR_PROC_KEY_XREF`,
    `PATNT_ENCNTR_KEY_XREF` — `bridge`, `low/medium`.
  - representative `joins` and a few `columns` selectivities.
  Row estimates are illustrative defaults, refinable later from live stats.
- **caboodle.yaml** seeds canonical dimensional tables: `EncounterFact`,
  `SurgicalCaseFact`, `DiagnosisEventFact`, `LabComponentResultFact` (`fact`, huge,
  high/very_high) and `PatientDim`, `DepartmentDim`, `ProviderDim`, `DateDim`
  (`dimension`, small/medium, low), with `*Fact->*Dim` joins.

### 2. Engine generalization (small, ~1 constant + 3 edits)
Today three rules hardcode `category == "clinical_event"`. Introduce
`EVENT_LIKE_CATEGORIES = {"clinical_event", "detail", "flowsheet", "result", "order", "fact"}`
(in `preflight/contracts.py`) and use it in:
- `estimate.py` — the `events` rollup (`_rows_for_category`) considers any event-like category.
- `risk.py` — the "unfiltered scan of clinical event table" rule fires for any event-like category.
- `optimize.py` — `event_tables` includes any event-like category.

This keeps the engine schema-agnostic; behavior for OMOP is unchanged (`clinical_event`
is still in the set).

### 3. Catalog-defaulted dialect
- `load_catalog` reads optional `default_dialect`; `Catalog` exposes `.default_dialect`
  (default `"generic"`).
- `cli.py`: change `--dialect` default to `None`; resolve effective dialect as
  `args.dialect or catalog.default_dialect or "generic"`. So
  `preflight check q.sql --catalog clarity` parses as T-SQL automatically.
- The library API is unchanged (`GeneratedSQL.dialect` still explicit); only the CLI gains
  the convenience.

### 4. Synthetic test fixture
`fixtures/queries/epic_or_cases.sql` — a small, hand-written T-SQL query using the same
patterns (a couple `*_DTL` joins to `ALL_*` dims and a `*_KEY_XREF`, a `dbo.` schema, a
`{fn ...}` escape, a single-quoted alias, a date filter). Contains no patient data.

### 5. Known v1 limitation (documented)
Derived-table aliases from `UNION ALL`/subqueries (e.g. `Table__1308`, `OR_KEY_XREF`) are
not resolved into lineage join edges (they are subqueries, not base tables). No crash;
base tables and join counts are still correct. Out of scope for Phase 1.

## Testing

- `tests/test_catalog_epic.py` — load `clarity` and `caboodle`; assert key tables exist
  with expected `volume`/`risk`; assert `default_dialect == "tsql"`.
- `tests/test_parse_tsql_epic.py` — parse the synthetic Epic query with `dialect="tsql"`;
  assert expected base tables and join count; assert `generic` raises `PreflightParseError`
  (locks in the dialect requirement).
- `tests/test_pipeline_epic.py` — run the synthetic query through `run_preflight` with the
  `clarity` catalog; assert risk is HIGH/CRITICAL (event-grain `*_DTL` + multi-join),
  the top bottleneck is a detail table, and optimizations are non-empty.
- `tests/test_cli.py` — add a case: `--catalog clarity` with no `--dialect` parses as tsql
  (assert success on the synthetic query file).
- Existing `tests/test_no_llm_guard.py` and all current tests must stay green.

## Out of scope (Phase 1)

Live SQL Server / Oracle connector (Phase 2), derived-table lineage resolution, naming-
convention auto-inference for unseeded Epic tables, real row-count calibration.

## Related: privacy follow-up (separate from this feature)

The 2026-06-01 privacy audit (`docs/privacy-and-data-handling-audit.md`) flagged that the
Postgres connector copies plan `Filter` predicate **literals** into `missing_index_hints`
(F1, Medium). Epic queries are literal-heavy, so this matters once the Phase-2 SQL Server
connector lands. Recommend fixing F1 (redact predicate RHS) before/with Phase 2; tracked
in the audit, not implemented here.
