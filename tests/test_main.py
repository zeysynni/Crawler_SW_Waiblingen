"""Tests for the CLI topic-selection logic in main.py.

select_topics() is the pure part of the CLI: given a Site and the raw --topics
string, it returns the Topic objects to crawl (or fails fast). We test it
without launching the agent/browser.
"""

import pytest

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
