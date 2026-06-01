from preflight.parse.sql import parse_sql
from preflight.catalog.loader import load_catalog
from preflight.lineage import build_lineage

SQL = """
SELECT p.person_id, m.value_as_number
FROM person p
JOIN measurement m ON p.person_id = m.person_id
WHERE m.measurement_concept_id = 3016723
"""


def test_lineage_nodes_annotated_from_catalog():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    lin = build_lineage(parsed, cat)
    by_table = {n.table: n for n in lin.nodes}
    assert by_table["measurement"].volume == "huge"
    assert by_table["measurement"].est_rows == 1_500_000_000
    assert by_table["person"].category == "demographics"


def test_lineage_edges_have_cardinality_transition():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    lin = build_lineage(parsed, cat)
    assert len(lin.edges) == 1
    edge = lin.edges[0]
    assert "fan-out" in edge.cardinality_transition.lower()


def test_lineage_captures_filters():
    parsed = parse_sql(SQL, dialect="duckdb")
    cat = load_catalog("omop")
    lin = build_lineage(parsed, cat)
    assert "measurement_concept_id" in lin.filters


def test_lineage_edges_follow_actual_join_graph():
    sql = (
        "SELECT p.person_id "
        "FROM person p "
        "JOIN measurement m ON p.person_id=m.person_id "
        "JOIN visit_occurrence v ON v.person_id=p.person_id"
    )
    parsed = parse_sql(sql, dialect="duckdb")
    cat = load_catalog("omop")
    lin = build_lineage(parsed, cat)
    edge_sets = {frozenset({e.source, e.target}) for e in lin.edges}
    assert edge_sets == {
        frozenset({"person", "measurement"}),
        frozenset({"person", "visit_occurrence"}),
    }
