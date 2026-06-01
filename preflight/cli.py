"""Command-line entry point for the Preflight analyzer."""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from preflight.catalog.loader import load_catalog
from preflight.contracts import GeneratedSQL
from preflight.pipeline import run_preflight
from preflight.report.render import render_json, render_text


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="preflight", description="SQL pre-execution analyzer")
    sub = parser.add_subparsers(dest="command", required=True)

    chk = sub.add_parser("check", help="Analyze a generated SQL file")
    chk.add_argument("sql_file", help="Path to a .sql file")
    chk.add_argument("--dialect", default="generic")
    chk.add_argument("--catalog", default="omop", help="Schema family catalog name")
    chk.add_argument("--target", default="generic", help="Execution-target label for the report")
    chk.add_argument("--format", choices=["text", "json"], default="text")

    # Live-plan source (optional). At most one — all are read-only, EXPLAIN-only.
    conn_grp = chk.add_mutually_exclusive_group()
    conn_grp.add_argument("--duckdb-fixture", action="store_true",
                          help="Attach the synthetic OMOP DuckDB connector for live plan analysis")
    conn_grp.add_argument("--duckdb-path", metavar="PATH",
                          help="Path to a local DuckDB database file (opened read-only)")
    conn_grp.add_argument("--postgres-dsn", metavar="DSN",
                          help="Postgres DSN (e.g. postgresql://user@host/db) for live EXPLAIN")

    args = parser.parse_args(argv)

    if args.command == "check":
        with open(args.sql_file, "r") as fh:
            query = fh.read()
        sql = GeneratedSQL(query=query, dialect=args.dialect, target=args.target)
        catalog = load_catalog(args.catalog)

        connector = None
        if args.duckdb_fixture:
            from fixtures.build_omop import build_omop_duckdb
            from preflight.connector.duckdb_connector import DuckDBConnector
            connector = DuckDBConnector(build_omop_duckdb())
        elif args.duckdb_path:
            import duckdb
            from preflight.connector.duckdb_connector import DuckDBConnector
            connector = DuckDBConnector(duckdb.connect(args.duckdb_path, read_only=True))
        elif args.postgres_dsn:
            from preflight.connector.postgres_connector import PostgresConnector
            connector = PostgresConnector(args.postgres_dsn)

        report = run_preflight(sql, catalog, connector=connector)
        out = render_json(report) if args.format == "json" else render_text(report)
        print(out)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
