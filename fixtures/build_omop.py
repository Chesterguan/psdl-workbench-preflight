"""Build a small, deterministic OMOP-shaped DuckDB for testing. No network."""
from __future__ import annotations

import os

import duckdb

QUERY_DIR = os.path.join(os.path.dirname(__file__), "queries")


def build_omop_duckdb(path: str = ":memory:"):
    con = duckdb.connect(path)
    con.execute("SELECT setseed(0.42)")
    con.execute("CREATE TABLE person(person_id INTEGER, year_of_birth INTEGER)")
    con.execute("CREATE TABLE visit_occurrence("
                "visit_occurrence_id INTEGER, person_id INTEGER, visit_concept_id INTEGER)")
    con.execute("CREATE TABLE measurement("
                "measurement_id INTEGER, person_id INTEGER, measurement_concept_id INTEGER, "
                "value_as_number DOUBLE, measurement_date DATE)")

    con.execute("INSERT INTO person SELECT range, 1950 + (range % 50) FROM range(500)")
    con.execute("INSERT INTO visit_occurrence "
                "SELECT range, range % 500, 9201 FROM range(1200)")
    con.execute(
        "INSERT INTO measurement SELECT range, range % 500, 3016723, "
        "10 + 5 * random(), DATE '2024-01-01' + INTERVAL (range % 120) DAY "
        "FROM range(40000)")
    return con


def load_query(name: str) -> str:
    with open(os.path.join(QUERY_DIR, f"{name}.sql"), "r") as fh:
        return fh.read()
