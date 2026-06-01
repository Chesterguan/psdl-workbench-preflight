# Epic Phase 2: Live SQL Server Plan Connector — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add a read-only SQL Server connector (`SET SHOWPLAN_XML ON`, estimated plan, no execution) that plugs into the existing `run_preflight(..., connector=...)` seam.

**Architecture:** A pure `parse_showplan_xml(xml) -> PlanFacts` parser + a `SQLServerConnector(dsn)` (lazy `pyodbc`). Predicate literals are redacted via a shared `redact_literals` (moved out of the Postgres connector). CLI gains `--sqlserver-dsn`. No engine/contract/pipeline changes.

**Tech Stack:** Python 3.9, `xml.etree.ElementTree`, optional `pyodbc`, pytest. Spec: `docs/superpowers/specs/2026-06-01-epic-edw-phase2-sqlserver-connector-design.md`.

**Conventions:** Work from `/Users/ziyuanguan/psdl-workbench-preflight`. Activate the venv every shell step: `. .venv/bin/activate`. Stage only the files named in each commit (never `git add -A`). End every commit message with:
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

### Task 1: Shared `redact_literals` (move out of postgres_connector)

**Files:**
- Create: `preflight/connector/redact.py`
- Modify: `preflight/connector/postgres_connector.py`
- Test: `tests/test_redact.py`

- [ ] **Step 1: Write the failing test**

`tests/test_redact.py`:
```python
from preflight.connector.redact import redact_literals


def test_redacts_string_and_numeric_literals():
    assert redact_literals("(person_id = 999000123)") == "(person_id = ?)"
    assert redact_literals("(pat_mrn = '1234567')") == "(pat_mrn = ?)"


def test_preserves_identifiers_with_digits():
    out = redact_literals("(order_results_2 = 42)")
    assert "order_results_2" in out and "42" not in out


def test_postgres_connector_reexports_redactor():
    # Existing test imports `_redact_literals` from postgres_connector — keep it working.
    from preflight.connector.postgres_connector import _redact_literals
    assert _redact_literals("(x = 5)") == "(x = ?)"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `. .venv/bin/activate && python -m pytest tests/test_redact.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.connector.redact'`

- [ ] **Step 3: Write the implementation**

`preflight/connector/redact.py`:
```python
"""Redact literal values from DB plan predicates before they reach reports (PHI safety)."""
from __future__ import annotations

import re

_STRING_LIT_RE = re.compile(r"'(?:[^']|'')*'")
_NUM_LIT_RE = re.compile(r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?")


def redact_literals(predicate: str) -> str:
    """Replace string/numeric literals in a predicate with '?'. Identifiers that merely
    contain digits (e.g. order_results_2) are preserved."""
    redacted = _STRING_LIT_RE.sub("?", predicate)
    redacted = _NUM_LIT_RE.sub("?", redacted)
    return redacted
```

In `preflight/connector/postgres_connector.py`, remove the `import re` line, the
`_STRING_LIT_RE`/`_NUM_LIT_RE` module constants, and the `_redact_literals` function
definition, and replace them with a re-export so existing call sites and tests keep working.
Near the top imports, add:
```python
from preflight.connector.redact import redact_literals as _redact_literals
```
Leave the call site `safe = _redact_literals(filt) if filt else ""` unchanged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && python -m pytest tests/test_redact.py tests/test_connector_postgres.py -q`
Expected: PASS (new redact tests + all existing Postgres connector tests, incl. the F1 redaction tests, still green)

- [ ] **Step 5: Commit**

```bash
git add preflight/connector/redact.py preflight/connector/postgres_connector.py tests/test_redact.py
git commit -m "refactor(preflight): shared redact_literals for plan predicates

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: SQL Server connector (`parse_showplan_xml` + `SQLServerConnector`)

**Files:**
- Create: `preflight/connector/sqlserver_connector.py`
- Test: `tests/test_connector_sqlserver.py`

- [ ] **Step 1: Write the failing test**

`tests/test_connector_sqlserver.py`:
```python
import os
import pytest

from preflight.connector.sqlserver_connector import parse_showplan_xml, SQLServerConnector

# A representative SHOWPLAN_XML (estimated plan, not executed). Namespaced like real output.
SHOWPLAN = """<?xml version="1.0"?>
<ShowPlanXML xmlns="http://schemas.microsoft.com/sqlserver/2004/07/showplan">
 <BatchSequence><Batch><Statements>
  <StmtSimple StatementText="SELECT ...">
   <QueryPlan>
    <MissingIndexes>
     <MissingIndexGroup Impact="85.0">
      <MissingIndex Database="[Clarity]" Schema="[dbo]" Table="[ORDER_PROCEDURE_DTL]">
       <ColumnGroup Usage="EQUALITY"><Column Name="[PROC_CD]"/></ColumnGroup>
      </MissingIndex>
     </MissingIndexGroup>
    </MissingIndexes>
    <RelOp NodeId="0" PhysicalOp="Hash Match" LogicalOp="Inner Join" EstimateRows="1200000">
     <RelOp NodeId="1" PhysicalOp="Index Seek" LogicalOp="Index Seek" EstimateRows="5000">
      <IndexScan><Object Table="[Clarity].[dbo].[ALL_PATIENTS]" Index="[PK_ALL_PATIENTS]"/></IndexScan>
     </RelOp>
     <RelOp NodeId="2" PhysicalOp="Clustered Index Scan" LogicalOp="Clustered Index Scan" EstimateRows="1200000">
      <TableScan>
       <Object Table="[Clarity].[dbo].[ORDER_PROCEDURE_DTL]"/>
       <Predicate><ScalarOperator ScalarString="[ORDER_PROCEDURE_DTL].[PROC_CD]='12345'"/></Predicate>
      </TableScan>
     </RelOp>
    </RelOp>
   </QueryPlan>
  </StmtSimple>
 </Statements></Batch></BatchSequence>
</ShowPlanXML>"""


def test_parse_showplan_extracts_facts():
    facts = parse_showplan_xml(SHOWPLAN)
    assert facts.total_estimated_rows == 1200000
    ops = {n.op for n in facts.nodes}
    assert {"Hash Match", "Index Seek", "Clustered Index Scan"} <= ops
    assert any(n.join_type for n in facts.nodes)   # Hash Match -> Inner Join
    assert any(n.scan_type for n in facts.nodes)   # Clustered Index Scan
    seek = next(n for n in facts.nodes if n.op == "Index Seek")
    assert seek.index_used is True
    assert seek.table == "ALL_PATIENTS"            # brackets + schema stripped
    scan = next(n for n in facts.nodes if n.op == "Clustered Index Scan")
    assert scan.table == "ORDER_PROCEDURE_DTL"


def test_missing_index_hint_from_showplan():
    facts = parse_showplan_xml(SHOWPLAN)
    joined = " ".join(facts.missing_index_hints)
    assert "ORDER_PROCEDURE_DTL" in joined and "PROC_CD" in joined


def test_scan_predicate_literal_is_redacted():
    facts = parse_showplan_xml(SHOWPLAN)
    joined = " ".join(facts.missing_index_hints)
    assert "12345" not in joined          # literal redacted (audit F1 carried forward)
    assert "PROC_CD" in joined            # column kept


def test_sqlserver_connector_constructs_without_driver():
    assert SQLServerConnector("Driver=...;Server=...;") is not None


@pytest.mark.skipif(not os.environ.get("PREFLIGHT_SQLSERVER_DSN"),
                    reason="set PREFLIGHT_SQLSERVER_DSN to run the live SQL Server test")
def test_live_sqlserver_showplan():
    facts = SQLServerConnector(os.environ["PREFLIGHT_SQLSERVER_DSN"]).analyze("SELECT 1")
    assert facts is not None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `. .venv/bin/activate && python -m pytest tests/test_connector_sqlserver.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'preflight.connector.sqlserver_connector'`

- [ ] **Step 3: Write the implementation**

`preflight/connector/sqlserver_connector.py`:
```python
"""SQL Server live connector. SET SHOWPLAN_XML ON returns the ESTIMATED plan; no execution."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Optional

from preflight.connector.base import PlanFacts
from preflight.connector.redact import redact_literals
from preflight.contracts import PlanNode

_JOIN_OPS = {"Hash Match", "Nested Loops", "Merge Join"}
_SCAN_PREDICATE_HINT_THRESHOLD = 1000


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _strip_brackets(name: str) -> str:
    # "[Clarity].[dbo].[ORDER_PROCEDURE_DTL]" -> "ORDER_PROCEDURE_DTL"
    return name.split(".")[-1].strip("[]")


def _own_descendants(relop):
    """Descendants of a RelOp, excluding anything inside nested child RelOps."""
    for child in list(relop):
        if _local(child.tag) == "RelOp":
            continue
        yield child
        yield from _own_descendants(child)


def _to_int(val) -> Optional[int]:
    try:
        return int(round(float(val)))
    except (TypeError, ValueError):
        return None


def parse_showplan_xml(xml_text: str) -> PlanFacts:
    root = ET.fromstring(xml_text)
    nodes: List[PlanNode] = []
    hints: List[str] = []

    for relop in (el for el in root.iter() if _local(el.tag) == "RelOp"):
        phys = relop.get("PhysicalOp", "?")
        logical = relop.get("LogicalOp", "")
        node = PlanNode(op=phys, estimated_rows=_to_int(relop.get("EstimateRows")))
        if "Scan" in phys:
            node.scan_type = phys
        if phys in _JOIN_OPS:
            node.join_type = logical or "Join"
        if "Seek" in phys or "Index" in phys:
            node.index_used = True

        predicate_text = None
        for d in _own_descendants(relop):
            lt = _local(d.tag)
            if lt == "Object" and node.table is None and d.get("Table"):
                node.table = _strip_brackets(d.get("Table"))
            if lt == "ScalarOperator" and predicate_text is None and d.get("ScalarString"):
                predicate_text = d.get("ScalarString")
        nodes.append(node)

        if "Scan" in phys and predicate_text and (node.estimated_rows or 0) > _SCAN_PREDICATE_HINT_THRESHOLD:
            hints.append(f"Scan on {node.table or '?'}; predicate {redact_literals(predicate_text)}")

    # total estimated rows = the root RelOp (direct child of QueryPlan)
    total: Optional[int] = None
    for qp in (el for el in root.iter() if _local(el.tag) == "QueryPlan"):
        for c in list(qp):
            if _local(c.tag) == "RelOp":
                total = _to_int(c.get("EstimateRows"))
                break
        if total is not None:
            break
    if total is None and nodes:
        total = nodes[0].estimated_rows

    # SQL Server's own missing-index suggestions (column names only — no literals)
    for mi in (el for el in root.iter() if _local(el.tag) == "MissingIndex"):
        tbl = _strip_brackets(mi.get("Table", "?"))
        cols = [_strip_brackets(c.get("Name")) for c in mi.iter()
                if _local(c.tag) == "Column" and c.get("Name")]
        hints.append(f"Missing index on {tbl} ({', '.join(cols)})")

    return PlanFacts(nodes=nodes, total_estimated_rows=total, missing_index_hints=hints)


class SQLServerConnector:
    def __init__(self, dsn: str):
        self._dsn = dsn

    def analyze(self, sql: str) -> PlanFacts:
        import pyodbc  # optional dependency, imported lazily
        with pyodbc.connect(self._dsn) as conn:
            cur = conn.cursor()
            cur.execute("SET SHOWPLAN_XML ON")
            try:
                cur.execute(sql)            # NOT executed — SHOWPLAN_XML returns the plan only
                row = cur.fetchone()
                xml_text = row[0] if row else ""
            finally:
                cur.execute("SET SHOWPLAN_XML OFF")
        return parse_showplan_xml(xml_text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && python -m pytest tests/test_connector_sqlserver.py -q`
Expected: PASS (4 tests run, 1 skipped — the live test, unless `$PREFLIGHT_SQLSERVER_DSN` is set)

- [ ] **Step 5: Commit**

```bash
git add preflight/connector/sqlserver_connector.py tests/test_connector_sqlserver.py
git commit -m "feat(preflight): SQL Server connector (SHOWPLAN_XML, estimated plan, no exec)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: CLI `--sqlserver-dsn` wiring + README

**Files:**
- Modify: `preflight/cli.py`
- Modify: `README.md`
- Test: `tests/test_cli_sqlserver.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_sqlserver.py`:
```python
import os
import tempfile
import pytest

from preflight.cli import main


def _write_query(text="SELECT 1 AS x"):
    fd, path = tempfile.mkstemp(suffix=".sql")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


def test_sqlserver_dsn_mutually_exclusive_with_duckdb_fixture():
    path = _write_query()
    with pytest.raises(SystemExit):  # argparse rejects two connector sources
        main(["check", path, "--catalog", "omop", "--dialect", "tsql",
              "--duckdb-fixture", "--sqlserver-dsn", "Driver=x;"])
    os.unlink(path)


def test_build_connector_resolves_sqlserver_dsn():
    # _build_connector should return a SQLServerConnector when --sqlserver-dsn is given,
    # without importing pyodbc (the driver import is deferred to .analyze()).
    from types import SimpleNamespace
    from preflight.cli import _build_connector
    from preflight.connector.sqlserver_connector import SQLServerConnector
    args = SimpleNamespace(duckdb_fixture=False, duckdb_path=None,
                           postgres_dsn=None, sqlserver_dsn="Driver=x;Server=y;")
    conn = _build_connector(args)
    assert isinstance(conn, SQLServerConnector)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `. .venv/bin/activate && python -m pytest tests/test_cli_sqlserver.py -q`
Expected: FAIL — `--sqlserver-dsn` is not a recognized argument / `_build_connector` doesn't handle it.

- [ ] **Step 3: Write the implementation**

In `preflight/cli.py`, add `--sqlserver-dsn` to the `check` subcommand's connector group
(alongside `--duckdb-fixture`/`--duckdb-path`/`--postgres-dsn`):
```python
    conn_grp.add_argument("--sqlserver-dsn", default=None,
                          help="SQL Server ODBC DSN for live SHOWPLAN_XML (read-only)")
```

In `_build_connector`, resolve it (after the Postgres branch, before `return None`):
```python
    sqlserver_dsn = getattr(args, "sqlserver_dsn", None) or os.environ.get("PREFLIGHT_SQLSERVER_DSN")
    ...
    if sqlserver_dsn:
        from preflight.connector.sqlserver_connector import SQLServerConnector
        return SQLServerConnector(sqlserver_dsn)
```
Place the `sqlserver_dsn = ...` line near the other DSN resolutions at the top of
`_build_connector`, and the `if sqlserver_dsn:` block immediately before `return None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `. .venv/bin/activate && python -m pytest tests/test_cli_sqlserver.py tests/test_cli.py tests/test_cli_config.py -q`
Expected: PASS (new tests + all existing CLI tests still green)

- [ ] **Step 5: Update README**

In `README.md`, under the "Epic EDW (T-SQL)" section, append:
```markdown
### Live query plan against SQL Server (read-only)
With `pip install pyodbc` and an ODBC driver, attach the real EDW for an estimated plan
(`SET SHOWPLAN_XML ON` — never executes the query):
```bash
preflight check report.sql --catalog clarity \
  --sqlserver-dsn "Driver={ODBC Driver 18 for SQL Server};Server=...;Database=Clarity;UID=...;PWD=..."
```
Or put `PREFLIGHT_SQLSERVER_DSN=...` in `.env` and just run `preflight check report.sql --catalog clarity`.
```

- [ ] **Step 6: Commit**

```bash
git add preflight/cli.py README.md tests/test_cli_sqlserver.py
git commit -m "feat(preflight): CLI --sqlserver-dsn for live SHOWPLAN_XML plans

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Full-suite verification

- [ ] **Step 1: Run the entire suite**

Run: `. .venv/bin/activate && python -m pytest -q`
Expected: all pass; live tests skipped (Postgres + SQL Server) unless their env DSNs are set.

- [ ] **Step 2: Quick offline CLI smoke (no connector) still works**

Run: `. .venv/bin/activate && python -m preflight.cli check fixtures/queries/epic_or_cases.sql --catalog clarity`
Expected: full report, Risk CRITICAL, dialect auto = tsql. (No commit; verification only.)

---

## Self-Review — Spec Coverage

- **Shared redaction (spec §1)** → Task 1. ✓
- **`parse_showplan_xml` + `SQLServerConnector` (spec §2)** → Task 2 (nodes, total, missing-index from `<MissingIndexes>`, redacted scan predicate, lazy pyodbc, `SET SHOWPLAN_XML ON/OFF`). ✓
- **CLI `--sqlserver-dsn` (spec §3)** → Task 3 (flag + env fallback + mutual exclusion). ✓
- **No pipeline/contract change (spec §4)** → confirmed; nothing in plan touches `pipeline.py`/`contracts.py`. ✓
- **Hermetic test + opt-in live** → Task 2 (captured SHOWPLAN + `$PREFLIGHT_SQLSERVER_DSN`-gated). ✓
- **Privacy: predicate redaction + DSN off CLI** → Task 1/2 (`redact_literals`) + `.env` fallback in Task 3. ✓

**Type/name consistency:** `redact_literals(str)->str` (Task 1) reused in Task 2; `parse_showplan_xml(str)->PlanFacts` and `SQLServerConnector(dsn).analyze(sql)->PlanFacts` match the `Connector` protocol and `PlanFacts`/`PlanNode` contracts; `_build_connector(args)` extended consistently with existing flag-resolution style.
