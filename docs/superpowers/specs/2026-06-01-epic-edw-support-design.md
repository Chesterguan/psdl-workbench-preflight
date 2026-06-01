# Epic EDW Support — Design (Phase 1)

**Date:** 2026-06-01
**Status:** Proposed — independently reviewed 2026-06-01 (findings applied); awaiting user sign-off
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

### 2. Output wording (small, OMOP jargon → schema-neutral) — review S1
The risk/optimize messages currently say "clinical event table" and "concept filter" (OMOP
vocabulary), which read wrong for an Epic analyst. Genericize in `risk.py` and `optimize.py`:
"clinical event table" → "high-volume event table"; "concept filter" → "a selective filter
(e.g. a code or date predicate)". Behavior unchanged; benefits all schemas. This is the only
engine-code edit in Phase 1.

### 3. Catalog-defaulted dialect
- `preflight/catalog/loader.py`: `Catalog.__init__` gains `default_dialect: str = "generic"`;
  `load_catalog` passes `default_dialect=data.get("default_dialect", "generic")` to the
  constructor and `Catalog` exposes it as `.default_dialect`. (Review S3 — explicit edit list.)
- `cli.py`: change `--dialect` default to `None`; resolve effective dialect as
  `args.dialect or catalog.default_dialect or "generic"`. So
  `preflight check q.sql --catalog clarity` parses as T-SQL automatically. (Existing
  `tests/test_cli.py` pass `--dialect` explicitly, so this default change is safe — review N1.)
- The library API is unchanged (`GeneratedSQL.dialect` still explicit); only the CLI gains
  the convenience.

### 4. Synthetic test fixture
`fixtures/queries/epic_or_cases.sql` — a small, hand-written T-SQL query using the same
patterns: a couple `*_DTL` joins to `ALL_*` dims and a `*_KEY_XREF`, a `dbo.` schema, a
single-quoted alias, a date filter, and — **required** — a 3-arg `CONVERT(varchar(N), col,
style)` (and/or `DATEADD/DATEDIFF`). Review M2: `{fn ...}` escapes alone parse fine under the
`generic` dialect, so the fixture must include `CONVERT`-style T-SQL syntax for the
"`generic` raises `PreflightParseError`" assertion to actually hold. Contains no patient data.

### 5. Known v1 limitation (documented)
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
- `tests/test_cli.py` — add a case: `--catalog clarity` with no `--dialect` parses as tsql
  (assert success on the synthetic query file).
- Existing `tests/test_no_llm_guard.py` and all current tests must stay green.

## Out of scope (Phase 1)

Live SQL Server / Oracle connector (Phase 2), derived-table lineage resolution, naming-
convention auto-inference for unseeded Epic tables, real row-count calibration.

## Related: privacy follow-up (separate from this feature)

The 2026-06-01 privacy audit (`docs/privacy-and-data-handling-audit.md`) flagged that the
Postgres connector copied plan `Filter` predicate **literals** into `missing_index_hints`
(F1, Medium). **F1 is now fixed** (commit `881ef5e`: `_redact_literals` strips literal values
to `?`). The same redaction must be applied to any predicate text the Phase-2 SQL Server
connector surfaces — carry this forward into the Phase-2 spec.
