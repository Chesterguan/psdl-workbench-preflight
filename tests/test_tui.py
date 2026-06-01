import io
from pathlib import Path

from rich.console import Console

from preflight.contracts import (
    PreflightReport, StudySummary, ScaleEstimate, RiskLevel, RuntimeCategory, Confidence,
)
from preflight.catalog.loader import load_catalog
from preflight.tui import verdict_for, render_report, triage_batch
from preflight.cli import main


def _rep(risk, runtime=RuntimeCategory.MODERATE, out=100):
    return PreflightReport(
        summary=StudySummary(execution_target="omop"),
        scale=ScaleEstimate(output_records=out),
        risk_level=risk, runtime_category=runtime, confidence=Confidence.MEDIUM,
    )


def _capture(fn) -> str:
    con = Console(file=io.StringIO(), width=120, force_terminal=False)
    fn(con)
    return con.file.getvalue()


def test_verdict_mapping():
    assert verdict_for(_rep(RiskLevel.LOW))[0] == "GO"
    assert "caution" in verdict_for(_rep(RiskLevel.MEDIUM))[0]
    assert verdict_for(_rep(RiskLevel.HIGH))[0] == "REVIEW"
    assert verdict_for(_rep(RiskLevel.CRITICAL))[0] == "BLOCK"


def test_runtime_extreme_escalates_borderline_medium():
    assert verdict_for(_rep(RiskLevel.MEDIUM, RuntimeCategory.EXTREME))[0] == "REVIEW"


def test_render_report_contains_verdict_and_fields():
    rep = _rep(RiskLevel.CRITICAL, RuntimeCategory.EXTREME, out=1_500_000_000)
    rep.risk_reasons = ["Very High risk table: measurement"]
    out = _capture(lambda c: render_report(c, rep, "demo.sql"))
    assert "BLOCK" in out and "CRITICAL" in out and "measurement" in out


def test_triage_batch_sorts_worst_first(tmp_path):
    (tmp_path / "low.sql").write_text("SELECT person_id FROM person WHERE person_id = 5")
    (tmp_path / "bad.sql").write_text("SELECT * FROM measurement")
    cat = load_catalog("omop")
    results = triage_batch([str(tmp_path / "low.sql"), str(tmp_path / "bad.sql")], cat, "duckdb")
    assert Path(results[0][0]).name == "bad.sql"          # CRITICAL sorts first
    assert results[0][1].risk_level == RiskLevel.CRITICAL


def test_triage_batch_tolerates_unanalyzable_file(tmp_path):
    # One bad (binary/unreadable) file must NOT abort the whole worklist.
    (tmp_path / "good.sql").write_text("SELECT * FROM measurement")
    (tmp_path / "bad.sql").write_bytes(b"\xff\xfe\x00 not text")
    cat = load_catalog("omop")
    res = triage_batch([str(tmp_path / "good.sql"), str(tmp_path / "bad.sql")], cat, "duckdb")
    assert len(res) == 2
    errors = [(p, e) for (p, rep, e) in res if e is not None]
    assert len(errors) == 1 and Path(errors[0][0]).name == "bad.sql"
    assert any(rep is not None for (_, rep, _) in res)  # good file still analyzed


def test_cli_tui_single_unreadable_returns_nonzero(tmp_path, capsys):
    f = tmp_path / "binary.sql"
    f.write_bytes(b"\xff\xfe\x00 not text")
    rc = main(["tui", str(f), "--catalog", "omop", "--dialect", "duckdb", "--no-input"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "could not analyze" in out.lower()


def test_cli_tui_single_non_interactive(tmp_path, capsys):
    f = tmp_path / "q.sql"
    f.write_text("SELECT * FROM measurement")
    rc = main(["tui", str(f), "--catalog", "omop", "--dialect", "duckdb", "--no-input"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "BLOCK" in out or "CRITICAL" in out


def test_cli_tui_batch_non_interactive(tmp_path, capsys):
    (tmp_path / "a.sql").write_text("SELECT * FROM measurement")
    (tmp_path / "b.sql").write_text("SELECT person_id FROM person WHERE person_id = 5")
    rc = main(["tui", str(tmp_path), "--catalog", "omop", "--dialect", "duckdb", "--no-input"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "worklist" in out.lower() and "a.sql" in out and "b.sql" in out
