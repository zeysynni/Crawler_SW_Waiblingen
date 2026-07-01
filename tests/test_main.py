"""Tests for the CLI topic-selection logic in main.py.

select_topics() is the pure part of the CLI: given a Site and the raw --topics
string, it returns the Topic objects to crawl (or fails fast). We test it
without launching the agent/browser.
"""

import asyncio

import pytest

import main
from config import Site, Topic
from main import select_topics


def make_site() -> Site:
    return Site(
        site="example",
        root_url="https://example.com",
        topics=[
            Topic(name="a", url="https://example.com/a"),
            Topic(name="b", url="https://example.com/b"),
            Topic(name="c", url="https://example.com/c"),
        ],
    )


def test_no_topics_arg_returns_all_topics():
    site = make_site()
    assert [t.name for t in select_topics(site, None)] == ["a", "b", "c"]


def test_empty_topics_arg_returns_all_topics():
    site = make_site()
    assert [t.name for t in select_topics(site, "")] == ["a", "b", "c"]


def test_subset_is_selected_in_given_order():
    site = make_site()
    assert [t.name for t in select_topics(site, "c,a")] == ["c", "a"]


def test_whitespace_around_names_is_tolerated():
    site = make_site()
    assert [t.name for t in select_topics(site, " a , b ")] == ["a", "b"]


def test_unknown_topic_name_fails_fast():
    site = make_site()
    with pytest.raises(KeyError, match="strm"):
        select_topics(site, "a,strm")


# --- crawl_topic retry behavior (backoff=0 so no real waiting) ---

def test_crawl_topic_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    async def flaky(agent, topic, root_url, make_pdf):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient")
        return {"pages": 1}

    monkeypatch.setattr(main, "process_topic", flaky)
    topic = Topic(name="a", url="https://example.com/a")
    result = asyncio.run(main.crawl_topic(None, topic, "https://example.com",
                                          False, attempts=3, backoff=0))
    assert result == {"pages": 1}
    assert calls["n"] == 3   # failed twice, succeeded on the third try


def test_crawl_topic_raises_after_exhausting_attempts(monkeypatch):
    calls = {"n": 0}

    async def always_fail(agent, topic, root_url, make_pdf):
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(main, "process_topic", always_fail)
    topic = Topic(name="a", url="https://example.com/a")
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(main.crawl_topic(None, topic, "https://example.com",
                                     False, attempts=2, backoff=0))
    assert calls["n"] == 2   # exactly `attempts` tries, then gives up (bounded)
