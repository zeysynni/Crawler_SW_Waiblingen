"""Tests for the crawl-config boundary: load_site() and its Pydantic models.

These cover the "validate at the boundary, reject bad input loudly" contract
(PLAN.md principle #3). They write tiny YAML files to a temp dir so they never
depend on sites/waiblingen.yaml and stay fast and isolated.
"""

import pytest
from pydantic import ValidationError

from config import Site, Topic, load_site


def write_yaml(tmp_path, text: str):
    """Write `text` to a temp .yaml file and return its path."""
    file = tmp_path / "site.yaml"
    file.write_text(text, encoding="utf-8")
    return file


VALID_YAML = """
site: example
root_url: https://example.com
topics:
  - name: home
    url: https://example.com
    instructions: crawl the homepage
  - name: contact
    path: [Service, Kontakt]
    instructions: click through to contact
"""


def test_valid_yaml_loads_into_typed_site(tmp_path):
    site = load_site(write_yaml(tmp_path, VALID_YAML))

    assert isinstance(site, Site)
    assert site.site == "example"
    assert len(site.topics) == 2
    assert all(isinstance(t, Topic) for t in site.topics)


def test_topic_lookup_returns_the_right_topic(tmp_path):
    site = load_site(write_yaml(tmp_path, VALID_YAML))

    assert site.topic("contact").path == ["Service", "Kontakt"]


def test_topic_with_neither_path_nor_url_is_rejected(tmp_path):
    bad = """
site: example
root_url: https://example.com
topics:
  - name: broken
    instructions: I have no path and no url
"""
    with pytest.raises(ValidationError, match="needs either 'path' or 'url'"):
        load_site(write_yaml(tmp_path, bad))


def test_topic_missing_required_name_is_rejected(tmp_path):
    bad = """
site: example
root_url: https://example.com
topics:
  - nme: typo-in-the-key
    url: https://example.com
"""
    with pytest.raises(ValidationError, match="name"):
        load_site(write_yaml(tmp_path, bad))


def test_unknown_topic_name_raises_keyerror(tmp_path):
    site = load_site(write_yaml(tmp_path, VALID_YAML))

    with pytest.raises(KeyError, match="nope"):
        site.topic("nope")


def test_missing_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_site(tmp_path / "does_not_exist.yaml")
