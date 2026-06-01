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


def test_empty_plan_returns_empty_facts():
    # A blank/absent plan (server returned no plan row) degrades gracefully, no exception.
    facts = parse_showplan_xml("")
    assert facts.nodes == [] and facts.total_estimated_rows is None
    assert parse_showplan_xml("   ").missing_index_hints == []


def test_sqlserver_connector_constructs_without_driver():
    assert SQLServerConnector("Driver=...;Server=...;") is not None


@pytest.mark.skipif(not os.environ.get("PREFLIGHT_SQLSERVER_DSN"),
                    reason="set PREFLIGHT_SQLSERVER_DSN to run the live SQL Server test")
def test_live_sqlserver_showplan():
    facts = SQLServerConnector(os.environ["PREFLIGHT_SQLSERVER_DSN"]).analyze("SELECT 1")
    assert facts is not None
