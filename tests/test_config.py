"""Tests for the site-config loader (allowlist YAML -> Pydantic models)."""

import pytest

from config import Section, Site, load_site

YAML = """\
root_url: https://www.example.de
sections:
  - path: Privatkunden/Strom
    subpages:
      - Ökostromtarif
  - path: Störung
    url: notfallnummern
  - path: Extern
    url: https://portal.example.net/app/
"""


def _write(tmp_path, text):
    p = tmp_path / "site.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_site_valid(tmp_path):
    site = load_site(_write(tmp_path, YAML))
    assert site.root_url == "https://www.example.de"
    assert [s.name for s in site.sections] == ["Privatkunden_Strom", "Störung", "Extern"]
    assert site.section("Privatkunden_Strom").subpages == ["Ökostromtarif"]


def test_base_url_variants():
    root = "https://www.example.de"
    assert Section(path="Privatkunden/Strom").base_url(root) == f"{root}/Privatkunden/Strom"
    assert Section(path="Störung", url="notfallnummern").base_url(root) == f"{root}/notfallnummern"
    assert Section(path="Extern", url="https://portal.example.net/app/").base_url(root) \
        == "https://portal.example.net/app/"


def test_stop_at_loads_and_defaults_to_empty(tmp_path):
    site = load_site(_write(tmp_path, YAML))
    assert site.stop_at == []                     # no key -> cut nothing

    with_stop = YAML.replace("sections:", "stop_at:\n  - '^##\\s+Weitere Links\\b'\nsections:")
    assert load_site(_write(tmp_path, with_stop)).stop_at == [r"^##\s+Weitere Links\b"]


def test_invalid_stop_at_regex_fails_at_load(tmp_path):
    """A broken pattern must fail here, not halfway through a crawl."""
    with pytest.raises(ValueError, match="invalid stop_at regex"):
        Site(root_url="https://x.de", stop_at=["^##[unclosed"], sections=[])

    bad = YAML.replace("sections:", "stop_at:\n  - '^##[unclosed'\nsections:")
    with pytest.raises(ValueError, match="invalid site config"):
        load_site(_write(tmp_path, bad))


def test_unknown_key_fails_loudly(tmp_path):
    bad = YAML.replace("subpages:", "subpage:")   # typo must not be silently dropped
    with pytest.raises(ValueError, match="invalid site config"):
        load_site(_write(tmp_path, bad))


def test_missing_file():
    with pytest.raises(FileNotFoundError):
        load_site("does/not/exist.yaml")


def test_unknown_section_name(tmp_path):
    site = load_site(_write(tmp_path, YAML))
    with pytest.raises(KeyError):
        site.section("Nope")
