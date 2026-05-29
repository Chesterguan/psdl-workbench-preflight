import os
import pytest

from preflight.connector.postgres_connector import parse_pg_plan, PostgresConnector

PG_PLAN = [{
    "Plan": {
        "Node Type": "Hash Join", "Join Type": "Inner", "Plan Rows": 4200,
        "Plans": [
            {"Node Type": "Seq Scan", "Relation Name": "measurement",
             "Plan Rows": 5000, "Filter": "(measurement_concept_id = 3016723)"},
            {"Node Type": "Index Scan", "Relation Name": "person",
             "Index Name": "person_pkey", "Plan Rows": 100},
        ],
    }
}]


def test_parse_pg_plan_extracts_facts():
    facts = parse_pg_plan(PG_PLAN)
    assert facts.total_estimated_rows == 4200
    ops = {n.op for n in facts.nodes}
    assert "Hash Join" in ops and "Seq Scan" in ops
    seq = next(n for n in facts.nodes if n.op == "Seq Scan")
    assert seq.table == "measurement"
    assert seq.estimated_rows == 5000
    idx = next(n for n in facts.nodes if n.op == "Index Scan")
    assert idx.index_used is True


def test_seq_scan_on_large_table_emits_missing_index_hint():
    facts = parse_pg_plan(PG_PLAN)
    assert any("measurement" in h for h in facts.missing_index_hints)


@pytest.mark.skipif(not os.environ.get("PREFLIGHT_PG_DSN"),
                    reason="set PREFLIGHT_PG_DSN to run live Postgres test")
def test_live_postgres_explain():
    conn = PostgresConnector(os.environ["PREFLIGHT_PG_DSN"])
    facts = conn.analyze("SELECT 1")
    assert facts is not None
