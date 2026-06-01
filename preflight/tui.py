"""Rich-based operations (Conduct of Operations) triage TUI over the Preflight
analyzer. Presentation only — all analysis goes through run_preflight."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Tuple

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from preflight.catalog.loader import Catalog
from preflight.connector.redact import redact_literals
from preflight.contracts import GeneratedSQL, PreflightReport, RiskLevel, RuntimeCategory
from preflight.pipeline import run_preflight

_RISK_STYLE = {
    RiskLevel.LOW: "green",
    RiskLevel.MEDIUM: "yellow",
    RiskLevel.HIGH: "dark_orange",
    RiskLevel.CRITICAL: "bold white on red",
}
_VERDICT = {
    RiskLevel.LOW: ("GO", "bold green"),
    RiskLevel.MEDIUM: ("GO (caution)", "green"),
    RiskLevel.HIGH: ("REVIEW", "dark_orange"),
    RiskLevel.CRITICAL: ("BLOCK", "bold white on red"),
}
_RISK_ORDER = {
    RiskLevel.CRITICAL: 0, RiskLevel.HIGH: 1, RiskLevel.MEDIUM: 2, RiskLevel.LOW: 3,
}


def verdict_for(report: PreflightReport) -> Tuple[str, str]:
    """Deterministic go/no-go label + rich style from risk level. An EXTREME runtime
    escalates a borderline MEDIUM to REVIEW."""
    label, style = _VERDICT[report.risk_level]
    if (report.risk_level == RiskLevel.MEDIUM
            and report.runtime_category == RuntimeCategory.EXTREME):
        return "REVIEW", "dark_orange"
    return label, style


def _fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else "n/a"


def analyze_file(path, catalog: Catalog, dialect: Optional[str] = None,
                 connector=None) -> PreflightReport:
    """Read a .sql file and run the full preflight pipeline over it."""
    query = Path(path).read_text()
    eff_dialect = dialect or catalog.default_dialect or "generic"
    sql = GeneratedSQL(query=query, dialect=eff_dialect, target=catalog.schema)
    return run_preflight(sql, catalog, connector=connector)


def analyze_file_safe(path, catalog: Catalog, dialect: Optional[str] = None,
                      connector=None) -> Tuple[Optional[PreflightReport], Optional[str]]:
    """Analyze a file, capturing any failure (unreadable/binary file, parse error) as a
    redacted message instead of raising — so one bad file can't abort a batch."""
    try:
        return analyze_file(path, catalog, dialect, connector), None
    except Exception as exc:  # noqa: BLE001 - any read/parse failure is reported, not raised
        return None, f"{type(exc).__name__}: {redact_literals(str(exc))}"


def render_report(console: Console, report: PreflightReport, title: str) -> None:
    """Render a single-query triage panel (verdict banner + key sections)."""
    verdict, vstyle = verdict_for(report)
    rstyle = _RISK_STYLE[report.risk_level]

    header = Text()
    header.append(f" {verdict} ", style=vstyle)
    header.append("   Risk: ")
    header.append(report.risk_level.value, style=rstyle)
    header.append(f"    Runtime: {report.runtime_category.value}")
    header.append(f"    Output ~{_fmt(report.scale.output_records)} rows")
    header.append(f"    Confidence: {report.confidence.value}")
    blocks: list = [header]

    if report.risk_reasons:
        why = Text("\nWhy:\n", style="bold")
        for r in report.risk_reasons:
            why.append(f"  - {r}\n")
        blocks.append(why)

    if report.bottlenecks:
        bt = Table(title="Bottlenecks", box=box.SIMPLE, title_style="bold")
        bt.add_column("Component")
        bt.add_column("Contribution", justify="right")
        bt.add_column("Reason")
        for b in report.bottlenecks[:5]:
            bt.add_row(b.component, f"{b.contribution_pct}%", b.reason)
        blocks.append(bt)

    if report.optimizations:
        opt = Text("\nOptimizations:\n", style="bold")
        for o in report.optimizations:
            opt.append(f"  - {o.action}\n")
        blocks.append(opt)

    if report.query_plan is not None:
        qp = report.query_plan
        pl = Text("\nLive Plan:\n", style="bold")
        pl.append(f"  total est {_fmt(qp.total_estimated_rows)} rows\n")
        for h in qp.missing_index_hints:
            pl.append(f"  - {h}\n")
        blocks.append(pl)

    if report.notes:
        nt = Text("\nNotes:\n", style="bold yellow")
        for n in report.notes:
            nt.append(f"  - {n}\n")
        blocks.append(nt)

    console.print(Panel(Group(*blocks), title=title, border_style=rstyle, box=box.ROUNDED))


def triage_batch(paths, catalog: Catalog, dialect: Optional[str] = None,
                 connector=None) -> List[Tuple[str, Optional[PreflightReport], Optional[str]]]:
    """Analyze each .sql path; return (path, report, error) sorted worst-first. Files that
    fail to analyze are kept (error set, report None) and sort to the top as needing attention."""
    results = [(str(p), *analyze_file_safe(p, catalog, dialect, connector)) for p in paths]

    def _key(item):
        _, rep, err = item
        if err is not None or rep is None:
            return (-1, 0)  # unanalyzable -> top of the worklist
        return (_RISK_ORDER[rep.risk_level], -(rep.scale.output_records or 0))

    results.sort(key=_key)
    return results


def render_batch_table(console: Console, results) -> None:
    t = Table(title="Preflight worklist (worst first)", box=box.SIMPLE_HEAVY)
    t.add_column("#", justify="right")
    t.add_column("Query")
    t.add_column("Verdict")
    t.add_column("Risk")
    t.add_column("Runtime")
    t.add_column("~Output", justify="right")
    for i, (path, rep, err) in enumerate(results, 1):
        if rep is None:
            t.add_row(str(i), Path(path).name, Text("ERROR", style="bold red"),
                      Text("—", style="red"), "—", "—")
            continue
        verdict, vstyle = verdict_for(rep)
        t.add_row(
            str(i), Path(path).name,
            Text(verdict, style=vstyle),
            Text(rep.risk_level.value, style=_RISK_STYLE[rep.risk_level]),
            rep.runtime_category.value, _fmt(rep.scale.output_records),
        )
    console.print(t)


def _sql_files_in(directory) -> List[str]:
    return sorted(str(p) for p in Path(directory).glob("*.sql"))


def run_tui(path, catalog: Catalog, dialect: Optional[str] = None, connector=None,
            interactive: Optional[bool] = None, console: Optional[Console] = None) -> int:
    """Entry point. Directory -> batch worklist (+ optional drill-in); file -> single view."""
    console = console or Console()
    if interactive is None:
        interactive = sys.stdin.isatty()

    p = Path(path)
    if p.is_dir():
        files = _sql_files_in(p)
        if not files:
            console.print(f"[yellow]No .sql files in {p}[/yellow]")
            return 1
        results = triage_batch(files, catalog, dialect, connector)
        render_batch_table(console, results)
        if interactive:
            from rich.prompt import Prompt
            while True:
                choice = Prompt.ask("pick # to view detail, or q to quit", default="q").strip()
                if choice.lower() in ("q", "quit", ""):
                    break
                if choice.isdigit() and 1 <= int(choice) <= len(results):
                    sel_path, sel_rep, sel_err = results[int(choice) - 1]
                    if sel_rep is None:
                        console.print(f"[red]Could not analyze {Path(sel_path).name}: {sel_err}[/red]")
                    else:
                        render_report(console, sel_rep, Path(sel_path).name)
        return 0

    if not p.is_file():
        console.print(f"[red]No such file or directory: {p}[/red]")
        return 1
    report, err = analyze_file_safe(p, catalog, dialect, connector)
    if err is not None:
        console.print(Panel(Text(f"Could not analyze {p.name}:\n{err}", style="red"),
                            title=p.name, border_style="red", box=box.ROUNDED))
        return 1
    render_report(console, report, p.name)
    return 0
