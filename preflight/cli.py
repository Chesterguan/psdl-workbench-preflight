"""Command-line entry point for the Preflight analyzer."""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from preflight.catalog.loader import load_catalog
from preflight.config import load_default_dotenvs
from preflight.contracts import GeneratedSQL
from preflight.pipeline import run_preflight
from preflight.report.render import render_json, render_text


def _build_connector(args):
    """Resolve a connector from flags or PREFLIGHT_* env, or None. Read-only / EXPLAIN-only."""
    duckdb_path = getattr(args, "duckdb_path", None) or os.environ.get("PREFLIGHT_DUCKDB_PATH")
    pg_dsn = getattr(args, "postgres_dsn", None) or os.environ.get("PREFLIGHT_PG_DSN")
    sqlserver_dsn = getattr(args, "sqlserver_dsn", None) or os.environ.get("PREFLIGHT_SQLSERVER_DSN")
    if getattr(args, "duckdb_fixture", False):
        from fixtures.build_omop import build_omop_duckdb
        from preflight.connector.duckdb_connector import DuckDBConnector
        return DuckDBConnector(build_omop_duckdb())
    if duckdb_path:
        import duckdb
        from preflight.connector.duckdb_connector import DuckDBConnector
        return DuckDBConnector(duckdb.connect(duckdb_path, read_only=True))
    if pg_dsn:
        from preflight.connector.postgres_connector import PostgresConnector
        return PostgresConnector(pg_dsn)
    if sqlserver_dsn:
        from preflight.connector.sqlserver_connector import SQLServerConnector
        return SQLServerConnector(sqlserver_dsn)
    return None


def _cmd_check(args) -> int:
    with open(args.sql_file, "r") as fh:
        query = fh.read()
    catalog_name = args.catalog or os.environ.get("PREFLIGHT_CATALOG") or "omop"
    catalog = load_catalog(catalog_name, catalog_dir=args.catalog_dir)
    dialect = (args.dialect or os.environ.get("PREFLIGHT_DIALECT")
               or catalog.default_dialect or "generic")
    target = args.target or catalog_name
    sql = GeneratedSQL(query=query, dialect=dialect, target=target)
    report = run_preflight(sql, catalog, connector=_build_connector(args))
    print(render_json(report) if args.format == "json" else render_text(report))
    return 0


def _cmd_catalog_bootstrap(args) -> int:
    from preflight.catalog.bootstrap import (
        DuckDBIntrospector, PostgresIntrospector, SQLServerIntrospector,
        bootstrap_catalog, to_yaml,
    )
    if args.duckdb_path:
        import duckdb
        introspector = DuckDBIntrospector(duckdb.connect(args.duckdb_path, read_only=True))
        default_dialect = "duckdb"
    elif args.postgres_dsn or os.environ.get("PREFLIGHT_PG_DSN"):
        introspector = PostgresIntrospector(args.postgres_dsn or os.environ["PREFLIGHT_PG_DSN"])
        default_dialect = "postgres"
    elif args.sqlserver_dsn or os.environ.get("PREFLIGHT_SQLSERVER_DSN"):
        introspector = SQLServerIntrospector(
            args.sqlserver_dsn or os.environ["PREFLIGHT_SQLSERVER_DSN"])
        default_dialect = "tsql"
    else:
        print("error: provide --duckdb-path, --postgres-dsn, or --sqlserver-dsn",
              file=sys.stderr)
        return 2

    stats = introspector.introspect()
    doc = bootstrap_catalog(stats, schema=args.schema_name, heuristic=args.heuristic,
                            default_dialect=default_dialect, stats_as_of=args.stats_as_of)
    text = to_yaml(doc)
    if args.stdout:
        print(text)
        return 0
    out_path = args.out
    if out_path is None:
        # Default to the user catalog dir so the new catalog is found transparently.
        catalog_dir = os.path.expanduser(
            os.environ.get("PREFLIGHT_CATALOG_DIR") or "~/.preflight/catalogs")
        os.makedirs(catalog_dir, exist_ok=True)
        out_path = os.path.join(catalog_dir, f"{args.schema_name}.yaml")
    with open(out_path, "w") as fh:
        fh.write(text)
    print(f"wrote {len(doc['tables'])} tables to {out_path}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    load_default_dotenvs()
    parser = argparse.ArgumentParser(prog="preflight", description="SQL pre-execution analyzer")
    sub = parser.add_subparsers(dest="command", required=True)

    chk = sub.add_parser("check", help="Analyze a generated SQL file")
    chk.add_argument("sql_file", help="Path to a .sql file")
    chk.add_argument("--dialect", default=None)
    chk.add_argument("--catalog", default=None, help="Schema family catalog name")
    chk.add_argument("--catalog-dir", default=None, help="Directory to resolve catalogs from")
    chk.add_argument("--target", default=None, help="Execution-target label for the report")
    chk.add_argument("--format", choices=["text", "json"], default="text")
    conn_grp = chk.add_mutually_exclusive_group()
    conn_grp.add_argument("--duckdb-fixture", action="store_true",
                          help="Attach the synthetic OMOP DuckDB connector")
    conn_grp.add_argument("--duckdb-path", default=None,
                          help="Local DuckDB file (opened read-only)")
    conn_grp.add_argument("--postgres-dsn", default=None,
                          help="Postgres DSN for live EXPLAIN")
    conn_grp.add_argument("--sqlserver-dsn", default=None,
                          help="SQL Server ODBC DSN for live SHOWPLAN_XML (read-only)")
    chk.set_defaults(func=_cmd_check)

    bs = sub.add_parser("catalog-bootstrap",
                        help="Generate a catalog YAML from a DB's system catalogs (read-only)")
    bs.add_argument("--schema-name", required=True, help="Catalog/schema name to emit")
    bs.add_argument("--heuristic", choices=["epic", "omop", "generic"], default="generic")
    bs.add_argument("--out", default=None,
                    help="Output YAML path (default: $PREFLIGHT_CATALOG_DIR/<schema-name>.yaml)")
    bs.add_argument("--stdout", action="store_true", help="Print YAML to stdout instead of writing")
    bs.add_argument("--stats-as-of", default=None, help="Freshness label to stamp into the YAML")
    src = bs.add_mutually_exclusive_group()
    src.add_argument("--duckdb-path", default=None)
    src.add_argument("--postgres-dsn", default=None)
    src.add_argument("--sqlserver-dsn", default=None)
    bs.set_defaults(func=_cmd_catalog_bootstrap)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
