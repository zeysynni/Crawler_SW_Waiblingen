"""Tests for the prompt builder: build_navigation() and get_user_prompt_structured_output().

These are pure functions (Topic -> string), so they test fast with no LLM or
browser. We check the navigation text for each case (absolute url, relative
url, click-path) and that the topic's instructions land in the full prompt.
"""

from config import Topic
from prompts import build_navigation, get_user_prompt_structured_output

ROOT = "https://www.stadtwerke-waiblingen.de"


def test_navigation_with_absolute_url_is_used_verbatim():
    topic = Topic(name="kontakt", url="https://www.stadtwerke-waiblingen.de/kontakt")
    nav = build_navigation(topic, ROOT)

    assert "https://www.stadtwerke-waiblingen.de/kontakt" in nav
    assert "Navigate directly" in nav


def test_navigation_with_relative_url_is_resolved_against_root():
    topic = Topic(name="kontakt", url="/kontakt")
    nav = build_navigation(topic, ROOT)

    assert "https://www.stadtwerke-waiblingen.de/kontakt" in nav


def test_navigation_with_path_lists_clicks_in_order():
    topic = Topic(name="strom", path=["Privatkunden", "Strom"])
    nav = build_navigation(topic, ROOT)

    assert ROOT in nav
    assert "'Privatkunden', then 'Strom'" in nav


def test_navigation_prefers_url_when_both_given():
    # Documented tiebreak: an explicit url wins over a click-path.
    topic = Topic(name="both", url="/kontakt", path=["Service", "Kontakt"])
    nav = build_navigation(topic, ROOT)

    assert "kontakt" in nav
    assert "click" not in nav.lower()


def test_full_prompt_includes_navigation_and_instructions():
    topic = Topic(
        name="strom",
        url="/Privatkunden/Strom",
        instructions="Crawl Strom top to bottom, expand every '+'.",
    )
    prompt = get_user_prompt_structured_output(topic, ROOT)

    assert "https://www.stadtwerke-waiblingen.de/Privatkunden/Strom" in prompt
    assert "Crawl Strom top to bottom, expand every '+'." in prompt
    assert "## Navigation" in prompt
    assert "## Structure and instructions" in prompt
