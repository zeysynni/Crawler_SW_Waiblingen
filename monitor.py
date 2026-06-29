"""Monitoring for the unattended crawl: detect regressions and alert via Pushover.

The pipeline runs without a human checking quality, and the target sites change
without notice. So after each topic we compare the new crawl to the previous one
and, if it lost pages / FAQs / most of its content, push an alert to the phone.
Configure with PUSHOVER_TOKEN and PUSHOVER_USER in .env.
"""

import json
import logging
import os
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger("crawler")


def send_pushover(message: str, title: str = "Crawler") -> bool:
    """Send a Pushover notification. No-op (warns) if creds aren't configured."""
    token = os.environ.get("PUSHOVER_TOKEN")
    user = os.environ.get("PUSHOVER_USER")
    if not token or not user:
        log.warning("Pushover not configured (PUSHOVER_TOKEN/USER); skipping notification")
        return False
    payload = urllib.parse.urlencode(
        {"token": token, "user": user, "message": message[:1000], "title": title}
    ).encode()
    try:
        with urllib.request.urlopen(
            "https://api.pushover.net/1/messages.json", data=payload, timeout=15
        ) as resp:
            return resp.status == 200
    except Exception as e:
        log.warning("Pushover send failed: %s", e)
        return False


def topic_metrics(data: dict) -> dict:
    """Coverage metrics for a crawl result, used to spot regressions."""
    pages = data.get("pages", [])
    faqs = sum(
        len(s["faqs"].get("QAs", []))
        for p in pages
        for b in p.get("blocks", [])
        for s in b.get("segments", [])
        if s.get("faqs")
    )
    files = sum(
        len([ln for ln in s["files"].splitlines() if ln.strip()])
        for p in pages
        for b in p.get("blocks", [])
        for s in b.get("segments", [])
        if s.get("files")
    )
    return {
        "pages": len(pages),
        "faqs": faqs,
        "files": files,
        "chars": len(json.dumps(data, ensure_ascii=False)),
    }


def run_summary(per_topic: list[tuple[str, dict]], failed: list[str]) -> str:
    """A short, detailed end-of-run summary: totals, per-topic counts, failures."""
    pages = sum(m["pages"] for _, m in per_topic)
    faqs = sum(m["faqs"] for _, m in per_topic)
    files = sum(m["files"] for _, m in per_topic)

    lines = [
        f"{len(per_topic)} ok, {len(failed)} failed",
        f"total: {pages} pages · {faqs} FAQ · {files} files",
    ]
    detail = [f"• {name}: {m['pages']}p, {m['faqs']} FAQ, {m['files']} files"
              for name, m in per_topic]
    # Include per-topic lines only if they fit comfortably in a push notification.
    if sum(len(d) for d in detail) < 700:
        lines += detail
    if failed:
        lines.append("failed: " + ", ".join(failed))
    return "\n".join(lines)


def regressions(old: dict | None, new: dict) -> list[str]:
    """Human-readable list of significant drops from `old` to `new` (empty = fine)."""
    if not old:
        return []  # no baseline yet — first crawl
    out = []
    if new["pages"] < old["pages"]:
        out.append(f"pages {old['pages']}→{new['pages']}")
    if old["faqs"] and new["faqs"] < old["faqs"] * 0.7:
        out.append(f"FAQ {old['faqs']}→{new['faqs']}")
    if old["chars"] and new["chars"] < old["chars"] * 0.6:
        out.append(f"content {old['chars']}→{new['chars']} chars")
    return out


def read_metrics(json_path: Path | str) -> dict | None:
    """Metrics for an existing crawl JSON, or None if it doesn't exist."""
    p = Path(json_path)
    if not p.exists():
        return None
    try:
        return topic_metrics(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None
