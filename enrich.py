"""Deterministic enrichment of crawl output.

The LLM agent generalizes across sites and adapts to layout changes (important
for a config-driven crawler whose targets change without notice). But its
capture of FAQ/accordion Q&As is *stochastic* — it misses some between runs.

So we ADD deterministically-extracted Q&As and files on top of the agent's
output (a union — we never remove what the agent found). On sites that match a
common expandable pattern this guarantees completeness; on exotic sites the
agent's own capture still carries it. Neither is a single point of failure.

This runs after the agent crawl, once per topic. It re-fetches each visited
page's HTML (cheap, no browser/LLM) and parses it with BeautifulSoup.
"""

import json
import logging
import re
import urllib.request
from pathlib import Path
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup, NavigableString

from pipeline import OUTPUT_DIR

log = logging.getLogger("crawler")


def fetch_html(url: str, timeout: int = 30) -> str:
    # Percent-encode non-ASCII characters in the URL (e.g. 'ä' in a path like
    # /Geschäftskunden/Strom) so urllib doesn't fail trying to ASCII-encode it.
    # `safe` keeps URL structure chars and '%' so already-encoded URLs are left as-is.
    url = quote(url, safe="/:?#[]@!$&'()*+,;=~%")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def _norm(text: str) -> str:
    """Normalize a question for dedup (lowercase, collapse whitespace)."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _soup(html: str) -> BeautifulSoup:
    """Parse HTML with <script>/<style>/<noscript> removed, so no extractor can
    scoop up inline JavaScript/CSS as if it were page content (e.g. the missed-
    section backstop grabbing a portal page's toastr script as 'prose')."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup


def resolve_subtopics(base_url: str, labels: list[str]) -> list[dict]:
    """Resolve subtopic *labels* to their sub-page URLs by matching link text on
    the base page — deterministic navigation, no LLM click-guessing.

    Returns ``[{"label", "url"}]`` for the labels found, in the given order.
    Prefers an exact link-text match, then a substring match. Unresolved labels
    are logged and skipped.
    """
    try:
        html = fetch_html(base_url)
    except Exception as e:
        log.warning("resolve_subtopics: could not fetch %s (%s)", base_url, e)
        return []
    soup = _soup(html)
    links = [(_norm(a.get_text(" ", strip=True)), a["href"])
             for a in soup.find_all("a", href=True) if a.get_text(strip=True)]

    resolved = []
    for label in labels:
        nl = _norm(label)
        # Match in priority tiers so a short nav link doesn't win over the real
        # content link: (1) exact text, (2) label is a substring of the link
        # text ("Fernwärme" in "Fernwärme Bedarfsgerecht und günstig"), then
        # (3) link text is a substring of the label, but only if it's a
        # substantial phrase (guards against "Wärme" matching "Fernwärme").
        href = (next((h for t, h in links if t == nl), None)
                or next((h for t, h in links if nl and nl in t), None)
                or next((h for t, h in links if nl and t in nl and len(t) >= 8), None))
        if href:
            resolved.append({"label": label, "url": urljoin(base_url, href)})
        else:
            log.warning("resolve_subtopics: no link found for '%s' on %s", label, base_url)
    return resolved


def _table_to_md(table) -> str:
    """Render an HTML <table> as a GitHub-flavored Markdown table."""
    rows = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * ncol) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


def _panel_text(panel) -> str:
    """Text of an expandable panel, with any <table> rendered as Markdown
    (instead of being flattened into one cell-per-line blob)."""
    if panel is None:
        return ""
    md_tables = []
    for t in panel.find_all("table"):
        md = _table_to_md(t)
        if md:
            md_tables.append(md)
        t.decompose()  # remove so it isn't also flattened into the plain text
    text = panel.get_text("\n", strip=True)
    return "\n\n".join(p for p in [text, *md_tables] if p).strip()


def extract_expandable_qas(html: str) -> list[dict]:
    """Every expandable question/answer pair, across common patterns.

    Covers Bootstrap accordions (`.accordion-item`) and native `<details>`.
    Deduped by question; skips entries whose answer is empty or echoes the
    question. Other widgets fall back to the LLM's own capture (union).
    """
    soup = _soup(html)
    qas: list[dict] = []
    seen: set[str] = set()

    def add(q: str, a: str) -> None:
        q, a = q.strip(), a.strip()
        if q and a and _norm(a) != _norm(q) and _norm(q) not in seen:
            seen.add(_norm(q))
            qas.append({"question": q, "answer": a})

    # Bootstrap accordions
    for item in soup.select(".accordion-item"):
        btn = item.select_one(".accordion-button")
        panel = item.select_one(".accordion-collapse")
        if btn:
            add(btn.get_text(" ", strip=True), _panel_text(panel))

    # Native <details>/<summary>
    for det in soup.select("details"):
        summary = det.find("summary")
        if not summary:
            continue
        q = summary.get_text(" ", strip=True)
        summary.extract()   # so the summary line isn't part of the answer
        add(q, _panel_text(det))

    return qas


def _section_heading(el) -> str:
    """Nearest preceding heading that is NOT an accordion's own button/title —
    so a panel is attributed to the section it sits under, not to the previous
    panel (accordion buttons are themselves headings)."""
    for prev in el.find_all_previous(["h1", "h2", "h3", "h4", "h5", "h6"]):
        if prev.find_parent(class_="accordion-item"):
            continue
        return prev.get_text(" ", strip=True)
    return ""


def extract_expandable_qa_groups(html: str) -> list[dict]:
    """Expandable panels grouped by the section they live under: [{heading, qas}].

    Same coverage as `extract_expandable_qas` (Bootstrap accordions + `<details>`)
    but keeps each panel under its real section heading and dedups only WITHIN a
    section — so repeated labels across sections (e.g. three products each with a
    "Technische Daten" panel) are all preserved, each in its own section.
    """
    soup = _soup(html)
    groups: list[dict] = []

    def add(heading: str, q: str, a: str) -> None:
        q, a = q.strip(), a.strip()
        if not (q and a and _norm(a) != _norm(q)):
            return
        g = next((x for x in groups if x["heading"] == heading), None)
        if g is None:
            g = {"heading": heading, "qas": [], "_seen": set()}
            groups.append(g)
        if _norm(q) not in g["_seen"]:
            g["_seen"].add(_norm(q))
            g["qas"].append({"question": q, "answer": a})

    for item in soup.select(".accordion-item"):
        btn = item.select_one(".accordion-button")
        if btn:
            add(_section_heading(item), btn.get_text(" ", strip=True),
                _panel_text(item.select_one(".accordion-collapse")))

    for det in soup.select("details"):
        summary = det.find("summary")
        if not summary:
            continue
        q = summary.get_text(" ", strip=True)
        heading = _section_heading(det)
        summary.extract()
        add(heading, q, _panel_text(det))

    return [{"heading": g["heading"], "qas": g["qas"]} for g in groups]


def extract_prose_sections(html: str) -> list[dict]:
    """Top-level (`<h2>`) prose sections in document order: {heading, text}.

    Skips sections that contain an accordion (those are handled by FAQ
    extraction). Used as a backstop to recover whole sections the LLM dropped.
    """
    soup = _soup(html)
    sections: list[dict] = []
    for h in soup.find_all("h2"):
        title = h.get_text(" ", strip=True)
        if not title:
            continue
        texts: list[str] = []
        has_accordion = False
        for node in h.next_elements:
            name = getattr(node, "name", None)
            if name == "h2":
                break  # reached the next section
            if name and "accordion-item" in (node.get("class") or []):
                has_accordion = True
            if isinstance(node, NavigableString):
                s = str(node).strip()
                if s:
                    texts.append(s)
        if has_accordion:
            continue  # FAQ section — handled elsewhere
        sections.append({"heading": title, "text": "\n".join(texts).strip()[:4000]})
    return sections


def _looks_like_prose(text: str) -> bool:
    """True if `text` is real explanatory prose, not an empty/link-label section.

    Drops URL/path tokens, then requires a decent length AND sentence
    punctuation — link lists ("Anmeldung/Einzug …") and empty file sections
    have neither, so they're skipped by the missed-section backstop.
    """
    cleaned = re.sub(r"\S*/\S*", " ", text)          # remove url/path tokens
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return len(cleaned) >= 40 and any(p in cleaned for p in ".?!")


def _llm_text_blob(page: dict) -> str:
    """All text the agent captured for a page, normalized — to detect what it missed."""
    parts: list[str] = []
    for block in page.get("blocks", []):
        parts.append(block.get("heading", "") or "")
        for seg in block.get("segments", []):
            parts.append(seg.get("subheading", "") or "")
            parts.append(seg.get("text", "") or "")
            if seg.get("faqs"):
                parts.append(seg["faqs"].get("title", "") or "")
                for qa in seg["faqs"].get("QAs", []):
                    parts.append(qa.get("question", ""))
    return _norm(" ".join(parts))


def _llm_text_raw(page: dict) -> str:
    """Raw (un-normalized) subheading+text the agent captured across the page —
    fed to `_content_norm` for content-level duplicate detection."""
    parts: list[str] = []
    for block in page.get("blocks", []):
        for seg in block.get("segments", []):
            parts.append(seg.get("subheading", "") or "")
            parts.append(seg.get("text", "") or "")
    return " ".join(parts)


def _content_norm(text: str) -> str:
    """Normalize for content comparison: drop Markdown table markup (pipes and
    '---' separator rows) then lowercase/collapse — so a panel rendered as a
    Markdown table compares equal to the same data written as tab-separated
    prose by the agent."""
    t = re.sub(r"-{2,}", " ", text or "")
    t = t.replace("|", " ")
    return _norm(t)


def _block_content_norm(block: dict) -> str:
    """Content-normalized subheading+text the agent captured in one block."""
    parts: list[str] = []
    for seg in block.get("segments", []):
        parts.append(seg.get("subheading", "") or "")
        parts.append(seg.get("text", "") or "")
    return _content_norm(" ".join(parts))


def _qa_already_present(qa: dict, blob_cn: str) -> bool:
    """True if the agent already wrote this panel's content into `blob_cn` — by
    its label (question) OR by a distinctive chunk of its answer — so the
    deterministic panel isn't added as a duplicate. When the agent missed it,
    this is False and the labelled panel is added."""
    q = _content_norm(qa.get("question", ""))
    a = _content_norm(qa.get("answer", ""))
    return bool((q and q in blob_cn) or (len(a) >= 15 and a[:40] in blob_cn))


def extract_file_groups(html: str) -> list[dict]:
    """PDF links grouped by their nearest preceding heading (h2–h5), in document
    order: ``[{heading, files:[names]}]``.

    This preserves the page's own layout — files under a "Downloads…" title stay
    under that title; files under a subtitle like "Ersatzversorgung" stay under
    it. Skips empty-text ghost links; dedupes by display name.
    """
    soup = _soup(html)
    groups: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        if ".pdf" not in a["href"].lower():
            continue
        name = a.get_text(" ", strip=True)
        if not name or name in seen:   # empty-text link = hidden/stale; skip
            continue
        seen.add(name)
        heading = ""
        for prev in a.find_all_previous(["h2", "h3", "h4", "h5"]):
            heading = prev.get_text(" ", strip=True)
            break
        if groups and groups[-1][0] == heading:
            groups[-1][1].append(name)
        else:
            groups.append((heading, [name]))
    return [{"heading": h, "files": f} for h, f in groups]


def extract_pdf_files(html: str) -> list[str]:
    """Flat list of every PDF display name on the page, deduped (used by tests)."""
    return [name for g in extract_file_groups(html) for name in g["files"]]


_WEEKDAYS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")


def extract_opening_hours(html: str) -> str:
    """Weekly opening hours from a ``<dl>`` (``<dt>`` day / ``<dd>`` times), as
    Markdown lines. Structured but agent-inconsistent (the times are often
    dropped), so recovered deterministically. Returns "" if no weekday schedule."""
    soup = _soup(html)
    for dl in soup.find_all("dl"):
        pairs = []
        for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
            day = dt.get_text(" ", strip=True)
            if _norm(day) not in _WEEKDAYS:
                continue
            times = re.sub(r"\s+", " ", dd.get_text(" ", strip=True))
            if times:
                pairs.append(f"- {day}: {times}")
        if len(pairs) >= 3:   # a real weekly schedule, not a stray <dl>
            return "\n".join(pairs)
    return ""


def _phone_key(phone: str) -> str:
    """National significant number of a phone: digits without the German country
    code (49) or a leading trunk 0. Lets '+49 7151 131-0' and '07151 131-0' —
    the same number in international vs national format — compare equal, and lets
    either form be found as a substring of the other in a page's digit blob."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("49"):
        return digits[2:]
    if digits.startswith("0"):
        return digits[1:]
    return digits


def extract_phone_contacts(html: str) -> list[dict]:
    """Every ``tel:`` phone link with a nearby label: ``[{label, phone, key}]``.

    Phone numbers are structured (``<a href="tel:…">``) and easy to miss in the
    LLM's prose capture, so we recover them deterministically. ``label`` is the
    nearest preceding heading/strong text (the contact's name/purpose); ``key``
    is the format-independent national number used for dedup/presence checks.
    """
    soup = _soup(html)
    out: list[dict] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        if not a["href"].lower().startswith("tel:"):
            continue
        phone = a.get_text(" ", strip=True) or a["href"][4:].strip()
        key = _phone_key(phone)
        if not key or key in seen:
            continue
        seen.add(key)
        label = ""
        for prev in a.find_all_previous(["h2", "h3", "h4", "h5", "strong", "b"]):
            t = prev.get_text(" ", strip=True)
            if t and len(t) < 60:
                label = t
                break
        out.append({"label": label, "phone": phone, "key": key})
    return out


def _locate_heading(page: dict, heading: str) -> tuple[int, int] | None:
    """Find where content under `heading` should attach: (block_idx, seg_pos).

    Matches a block heading (attach at end of that block) or a segment
    subheading (attach right after it). Uses normalized equality or containment
    with a length guard, so a short heading like "Strom" doesn't swallow a longer
    "Downloads Strom …". Returns None if nothing matches.
    """
    nh = _norm(heading)
    if not nh:
        return None

    def match(a: str, b: str) -> bool:
        return bool(a) and bool(b) and (a == b or (a in b and len(a) >= 8) or (b in a and len(b) >= 8))

    for bi, b in enumerate(page.get("blocks", [])):
        if match(_norm(b.get("heading", "")), nh):
            return (bi, len(b.get("segments", [])))
    for bi, b in enumerate(page.get("blocks", [])):
        for si, seg in enumerate(b.get("segments", [])):
            if match(_norm(seg.get("subheading", "")), nh):
                return (bi, si + 1)
    return None


def _strip_redundant_faq(page: dict, det_norms: set[str]) -> int | None:
    """Remove the agent's representations of questions the deterministic set covers.

    Drops matching faqs QAs, bare question lines inside text, and a subheading
    that is itself one of the questions — so the authoritative FAQ block added
    afterwards doesn't duplicate them.

    Returns the index of the block that is a GENUINE FAQ section — one the agent
    labelled "FAQ"/"Fragen" or that holds an actual ``faqs`` segment — so the
    clean FAQ can be inserted there. An incidental panel-label match (e.g. a
    content accordion "Technische Daten" written into prose) is still stripped to
    avoid duplication, but does NOT make the block a FAQ anchor; that lets such
    content-panels fall through to per-section attachment instead of being
    dumped into one bucket. Returns None if there is no genuine FAQ section.
    """
    anchor: int | None = None
    for idx, block in enumerate(page.get("blocks", [])):
        is_faq_section = bool(re.search(r"faq|fragen", _norm(block.get("heading", ""))))
        for seg in block.get("segments", []):
            if seg.get("faqs"):
                kept = [qa for qa in seg["faqs"].get("QAs", [])
                        if _norm(qa.get("question", "")) not in det_norms]
                seg["faqs"] = {"title": seg["faqs"].get("title"), "QAs": kept} if kept else None
                is_faq_section = True
            if seg.get("text"):
                lines = [ln for ln in seg["text"].splitlines() if _norm(ln) not in det_norms]
                seg["text"] = "\n".join(lines).strip() or None
            sub = seg.get("subheading")
            if sub and _norm(sub) in det_norms:
                seg["subheading"] = None       # strip the redundant label…
            elif sub and re.search(r"faq|fragen", _norm(sub)):
                is_faq_section = True          # …but only faq/fragen marks a section
        if is_faq_section and anchor is None:
            anchor = idx
    return anchor


def _strip_redundant_files(page: dict) -> None:
    """Clear the agent's file lists (deterministic extraction is authoritative)
    and drop any now-empty 'Downloads' block, so the real files can be re-attached
    per section without duplicating the agent's (possibly stale) lists."""
    kept: list[dict] = []
    for block in page.get("blocks", []):
        for seg in block.get("segments", []):
            seg["files"] = None
        is_dl = _norm(block.get("heading", "")).startswith("download")
        has_content = any(
            seg.get("subheading") or seg.get("text") or seg.get("contacts") or seg.get("faqs")
            for seg in block.get("segments", [])
        )
        if is_dl and not has_content:
            continue  # an emptied "Downloads…" block — drop it (re-added per group below)
        kept.append(block)
    page["blocks"] = kept


def _is_blank_text(text: str | None) -> bool:
    """True if `text` carries no real content: None, whitespace, or only empty
    list markers ('- ', '* ', '1. ') — the placeholder the agent leaves behind
    when the links/files that filled a list get stripped and re-attached."""
    if not text:
        return True
    stripped = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", text.strip(), flags=re.M)
    return not stripped.strip()


_MARKUP_LINE_RE = re.compile(r"<\s*/?\s*(?:script|style|link|meta|head|body|html|!)", re.I)


def _strip_markup_lines(text: str | None) -> str | None:
    """Drop lines the agent scraped from raw page source — <script>/<style>/<link>
    tags and IE conditional-comment remnants ('[if lt IE 9]>', '<![endif]') — which
    show up when it over-captures a messy page's <head> (e.g. a login portal).
    Real prose never contains these, so this is safe."""
    if not text:
        return text
    kept = []
    for ln in text.splitlines():
        low = ln.strip().lower()
        if _MARKUP_LINE_RE.search(low):
            continue
        if low.startswith("[if ") or "endif]" in low:
            continue
        kept.append(ln)
    return "\n".join(kept).strip() or None


def _prune_empty_segments(page: dict) -> None:
    """Remove agent noise: raw-source markup lines are stripped, blank text is
    nulled, and segments (then blocks) left with no content at all are dropped —
    so re-attaching files elsewhere doesn't leave an empty '- \\n- \\n-'
    placeholder, and scraped <script>/<head> cruft never reaches the output."""
    for block in page.get("blocks", []):
        kept = []
        for seg in block.get("segments", []):
            seg["text"] = _strip_markup_lines(seg.get("text"))
            if _is_blank_text(seg.get("text")):
                seg["text"] = None
            if any(seg.get(k) for k in ("subheading", "text", "files", "contacts", "faqs")):
                kept.append(seg)
        block["segments"] = kept
    page["blocks"] = [b for b in page.get("blocks", []) if b.get("segments")]


def _page_title(html: str) -> str:
    """A human-readable page name from <title> (sans site suffix), else <h1>."""
    soup = _soup(html)
    if soup.title and soup.title.get_text(strip=True):
        return soup.title.get_text(strip=True).split("|")[0].strip()
    h1 = soup.find("h1")
    return h1.get_text(" ", strip=True) if h1 else ""


def enrich_topic(topic: str, output_dir: Path | str = OUTPUT_DIR) -> None:
    """Add deterministically-found FAQ Q&As and files the agent missed (union).

    For each page in ``<output_dir>/<topic>.json``: re-fetch the HTML, extract
    expandable Q&As and PDF files, and append only the ones NOT already captured
    by the agent. The agent's output is never removed — so if the deterministic
    pass finds nothing (exotic site), nothing is lost.
    """
    path = Path(output_dir) / f"{topic}.json"
    if not path.exists():
        log.warning("enrich: %s not found, skipping", path)
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    for page in data.get("pages", []):
        url = page.get("url")
        if not url:
            continue
        try:
            html = fetch_html(url)
        except Exception as e:
            log.warning("enrich: could not fetch %s (%s); keeping agent output", url, e)
            continue

        page["title"] = _page_title(html)

        det_qas = extract_expandable_qas(html)
        det_norms = {_norm(qa["question"]) for qa in det_qas}

        # Deterministic accordion recovery. Strip the agent's redundant
        # representations of these questions (partial faqs entries, bare question
        # lines, question-as-subheading), then attach each panel to the SECTION it
        # lives under — one unified path. Real FAQ sections ("Sie haben Fragen?")
        # land their Q&As under that heading; per-product content panels (three
        # "Technische Daten") each stay under their own product. No single-bucket
        # dump, so nothing is mis-grouped or lost.
        _strip_redundant_faq(page, det_norms)

        page.setdefault("blocks", [])
        page_cn = _content_norm(_llm_text_raw(page))
        for g in extract_expandable_qa_groups(html):
            loc = _locate_heading(page, g["heading"])
            if loc is not None:
                bi, pos = loc
                # Union, don't duplicate: skip panels whose content the agent
                # already wrote into this block (as a label, or as label-less
                # prose/tab-separated data).
                blob = _block_content_norm(page["blocks"][bi])
                qas = [qa for qa in g["qas"] if not _qa_already_present(qa, blob)]
                if qas:
                    page["blocks"][bi].setdefault("segments", []).insert(
                        pos, {"faqs": {"title": None, "QAs": qas}})
            else:
                # No matching block: keep the content under its own heading, but
                # still skip anything already present elsewhere on the page.
                qas = [qa for qa in g["qas"] if not _qa_already_present(qa, page_cn)]
                if qas:
                    page["blocks"].append(
                        {"heading": g["heading"] or "FAQ",
                         "segments": [{"faqs": {"title": None, "QAs": qas}}]})

        # Contacts: recover phone numbers (tel: links) the agent missed. Matched
        # by digits so we don't duplicate ones it already captured; attached to a
        # contact-ish block if present, else the last block (contacts sit at the
        # page bottom) — no injected title.
        det_phones = extract_phone_contacts(html)
        page.setdefault("blocks", [])
        # A phone counts as present if its national number (country-code/leading-0
        # stripped) appears anywhere in the page's digit blob — so a number the
        # agent captured in either format isn't re-added.
        digit_blob = re.sub(r"\D", "", json.dumps(page, ensure_ascii=False))
        missing = [p for p in det_phones if p["key"] not in digit_blob]
        if missing and page["blocks"]:
            lines = [f"{p['label']}: {p['phone']}" if p["label"] else p["phone"] for p in missing]
            target = next((bi for bi, b in enumerate(page["blocks"])
                           if re.search(r"kontakt|erreichen|kunden-?center|servicecenter",
                                        _norm(b.get("heading", "")))),
                          len(page["blocks"]) - 1)
            page["blocks"][target].setdefault("segments", []).append({"contacts": "\n".join(lines)})

        # Opening hours: structured (<dl>) but the agent often drops the actual
        # times. Recover them if the agent didn't capture the weekly schedule
        # (fewer than 3 weekdays present), attaching to the "Öffnungszeiten" block
        # if present, else a contact block, else the last block.
        det_hours = extract_opening_hours(html)
        if det_hours and page["blocks"]:
            page_text = _norm(json.dumps(page, ensure_ascii=False))
            if sum(1 for d in _WEEKDAYS if d in page_text) < 3:
                target = next(
                    (bi for bi, b in enumerate(page["blocks"])
                     if "öffnungszeit" in _norm(b.get("heading", ""))
                     or any("öffnungszeit" in _norm(s.get("subheading", "")) for s in b.get("segments", []))),
                    None)
                if target is None:
                    target = next((bi for bi, b in enumerate(page["blocks"])
                                   if re.search(r"kontakt|erreichen", _norm(b.get("heading", "")))),
                                  len(page["blocks"]) - 1)
                page["blocks"][target].setdefault("segments", []).append(
                    {"subheading": "Öffnungszeiten", "text": det_hours})

        # Files: deterministic extraction is authoritative (the agent's file
        # lists can include stale/ghost links). Strip the agent's files, then
        # re-attach each PDF group under its own heading — files under a
        # "Downloads…" title stay there; files under a subtitle like
        # "Ersatzversorgung" stay under it (preserving the page's layout).
        det_groups = extract_file_groups(html)
        det_files_n = sum(len(g["files"]) for g in det_groups)
        if det_groups:
            _strip_redundant_files(page)
            for g in det_groups:
                files_seg = {"files": "\n".join(g["files"])}
                loc = _locate_heading(page, g["heading"])
                if loc is not None:
                    bi, pos = loc
                    segs = page["blocks"][bi].setdefault("segments", [])
                    # If the group's own title (e.g. "Downloads zur Grundversorgung")
                    # is more specific than the block/subheading it attaches under,
                    # keep it as a subheading so the page's real title isn't lost.
                    prev_sub = segs[pos - 1].get("subheading") if 0 < pos <= len(segs) else None
                    gh = g["heading"]
                    already = _norm(gh) in (_norm(page["blocks"][bi].get("heading", "")),
                                            _norm(prev_sub or ""))
                    inject = [] if (already or not gh) else [{"subheading": gh}]
                    segs[pos:pos] = inject + [files_seg]
                else:
                    # No matching section — add a block under the group's own heading.
                    page["blocks"].append(
                        {"heading": g["heading"] or "Downloads", "segments": [files_seg]}
                    )

        # Backstop: recover whole <h2> sections the agent dropped entirely,
        # inserted in document order (after the nearest preceding section the
        # agent did capture).
        blob = _llm_text_blob(page)
        sections = extract_prose_sections(html)
        added = 0
        for i, sec in enumerate(sections):
            if (_norm(sec["heading"]) in blob
                    or _norm(sec["heading"]).startswith("download")  # file section, handled above
                    or not _looks_like_prose(sec["text"])):
                continue  # captured already, a downloads section, or not substantial prose
            insert_at = len(page["blocks"])
            for j in range(i - 1, -1, -1):
                prev = _norm(sections[j]["heading"])
                idx = next((bi for bi, b in enumerate(page["blocks"])
                            if prev and prev in _norm(b.get("heading", ""))), None)
                if idx is not None:
                    insert_at = idx + 1
                    break
            page["blocks"].insert(
                insert_at, {"heading": sec["heading"], "segments": [{"text": sec["text"]}]}
            )
            added += 1

        _prune_empty_segments(page)   # drop leftover empty-list/whitespace placeholders

        log.info("enrich %s: %d FAQ, %d files, %d contacts, +%d sections",
                 url, len(det_qas), det_files_n, len(missing), added)

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
