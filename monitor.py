"""Monitoring for the unattended crawl: detailed run report + Pushover alert.

The pipeline runs without a human checking quality, and the target site
changes without notice. After each run we report, per page: success/failure,
the failure reason, start time and duration, output size — and compare the
new clean markdown against the previous run's file to flag regressions
(content shrank a lot / sections vanished). Configure with PUSHOVER_TOKEN and
PUSHOVER_USER in .env; without them the report only goes to the log.
"""

import logging
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime

log = logging.getLogger("crawler")

_PUSHOVER_LIMIT = 1024   # Pushover message size cap


def send_pushover(message: str, title: str = "Crawler") -> bool:
    """Send a Pushover notification. No-op (warns) if creds aren't configured."""
    token = os.environ.get("PUSHOVER_TOKEN")
    user = os.environ.get("PUSHOVER_USER")
    if not token or not user:
        log.warning("Pushover not configured (PUSHOVER_TOKEN/USER); skipping notification")
        return False
    payload = urllib.parse.urlencode(
        {"token": token, "user": user, "message": message[:_PUSHOVER_LIMIT], "title": title}
    ).encode()
    try:
        with urllib.request.urlopen(
            "https://api.pushover.net/1/messages.json", data=payload, timeout=15
        ) as resp:
            return resp.status == 200
    except Exception as e:
        log.warning("Pushover send failed: %s", e)
        return False


# --- metrics & regressions ----------------------------------------------------

def md_metrics(md_text: str) -> dict:
    """Coverage metrics of a clean markdown file, used to spot regressions."""
    return {
        "chars": len(md_text),
        "sections": len(re.findall(r"^#{1,6}\s", md_text, flags=re.MULTILINE)),
    }


def regressions(old: dict | None, new: dict) -> list[str]:
    """Human-readable list of significant drops from `old` to `new` (empty = fine)."""
    if not old:
        return []  # no baseline yet — first crawl of this page
    out = []
    if old["sections"] and new["sections"] < old["sections"] * 0.7:
        out.append(f"sections {old['sections']}→{new['sections']}")
    if old["chars"] and new["chars"] < old["chars"] * 0.6:
        out.append(f"content {old['chars']}→{new['chars']} chars")
    return out


# --- run report -----------------------------------------------------------------

def _hhmm(dt: datetime | None) -> str:
    return dt.astimezone().strftime("%H:%M:%S") if dt else "?"


def run_report(pages: list, started: datetime, finished: datetime,
               upload: dict | None = None) -> str:
    """Detailed end-of-run report from `crawl.PageResult`-shaped objects that
    additionally carry `.clean_chars` and `.regression` (set by main.py).

    Full report — the log gets all of it; Pushover truncates at 1024 chars,
    so the order puts what matters first: the files an upload actually
    changed remotely (`upload` is `uploader.upload_pages`'s summary dict —
    "new:" uploaded, "pruned:" deleted; unchanged files are not listed),
    then failures (with reasons), then regressions/notes, then the per-page
    success lines.
    """
    ok = [p for p in pages if p.ok]
    failed = [p for p in pages if not p.ok]
    regressed = [p for p in ok if getattr(p, "regression", None)]
    noted = [p for p in pages if p.notes]

    lines = [
        f"{len(ok)} ok, {len(failed)} failed"
        + (f", {len(regressed)} regressed" if regressed else ""),
        f"run {_hhmm(started)}–{_hhmm(finished)}"
        f" ({(finished - started).total_seconds():.0f}s)",
    ]
    if upload:
        lines += [f"new: {name}" for name in upload["uploaded"]]
        lines += [f"pruned: {name}" for name in upload["pruned"]]
    for p in failed:
        lines.append(f"✗ {p.name} at {_hhmm(p.started_at)} ({p.duration:.0f}s): {p.error}")
    for p in regressed:
        lines.append(f"⚠ {p.name}: {', '.join(p.regression)}")
    for p in noted:
        for note in p.notes:
            lines.append(f"⚠ {p.name}: {note}")
    for p in ok:
        lines.append(
            f"✓ {p.name} at {_hhmm(p.started_at)} ({p.duration:.1f}s, "
            f"{getattr(p, 'clean_chars', 0)} chars)"
        )
    return "\n".join(lines)
