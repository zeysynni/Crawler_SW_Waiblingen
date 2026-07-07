"""Tests for run metrics, regression detection, and the run report."""

from datetime import datetime, timedelta, timezone

from crawl import PageResult
from monitor import md_metrics, regressions, run_report


def test_md_metrics_counts_chars_and_sections():
    m = md_metrics("# A\ntext\n## B\nmore\n### C\n")
    assert m["sections"] == 3
    assert m["chars"] > 0


def test_regressions_flags_big_drops_only():
    old = {"chars": 1000, "sections": 10}
    assert regressions(old, {"chars": 950, "sections": 10}) == []          # noise
    assert regressions(old, {"chars": 400, "sections": 10}) != []          # content drop
    assert regressions(old, {"chars": 1000, "sections": 5}) != []          # sections drop
    assert regressions(None, {"chars": 1, "sections": 0}) == []            # no baseline


def _page(name, *, error=None, notes=(), seconds=2.0):
    t0 = datetime(2026, 7, 7, 6, 0, 0, tzinfo=timezone.utc)
    p = PageResult(name=name, url=f"https://x.de/{name}", error=error,
                   started_at=t0, finished_at=t0 + timedelta(seconds=seconds))
    p.notes = list(notes)
    p.clean_chars = 1234
    p.regression = []
    return p


def test_run_report_details():
    ok = _page("Privatkunden_Strom")
    bad = _page("Netze_Gasnetz", error="Timeout 30s exceeded", seconds=30)
    warn = _page("Privatkunden_Waerme", notes=["no link with text 'Fernwärme' on https://x.de"])
    regressed = _page("Kontakt")
    regressed.regression = ["content 2000→900 chars"]

    t0 = ok.started_at
    report = run_report([ok, bad, warn, regressed], t0, t0 + timedelta(seconds=60))

    assert "3 ok, 1 failed, 1 regressed" in report
    assert "✗ Netze_Gasnetz" in report and "Timeout 30s exceeded" in report
    assert "⚠ Privatkunden_Waerme: no link with text 'Fernwärme'" in report
    assert "⚠ Kontakt: content 2000→900 chars" in report
    assert "✓ Privatkunden_Strom" in report and "1234 chars" in report
    assert "(60s)" in report
    # failures come before success lines so truncation never hides them
    assert report.index("✗ Netze_Gasnetz") < report.index("✓ Privatkunden_Strom")
