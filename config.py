"""Crawl-target configuration.

*What* to crawl is data: it lives in ``sites/*.yaml``. *How* to crawl is code.
This module only defines the typed shape of that data and knows how to load it.

A site file is an **allowlist**: pages not claimed here are never crawled.

    root_url: https://www.stadtwerke-waiblingen.de
    sections:
      - path: Privatkunden/Strom        # base page; also names outputs/hierarchy
        subpages:                       # sub-pages by their visible link text
          - Ökostromtarif
          - Grundversorgung
      - path: Störung                   # display/file name ...
        url: notfallnummern             # ... fetched from a different URL
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from clean import slug


class Section(BaseModel):
    """One base page plus the sub-pages (by visible link text) to crawl from it."""

    # Reject unknown keys so a typo like `subpage:` fails loudly instead of
    # silently dropping the value.
    model_config = ConfigDict(extra="forbid")

    path: str                          # names the output file + site hierarchy
    url: str | None = None             # fetch override (relative to root_url, or absolute)
    subpages: list[str] = Field(default_factory=list)

    @property
    def name(self) -> str:
        """Output-file base name, e.g. 'Privatkunden/Strom' -> 'Privatkunden_Strom'."""
        return slug(self.path.strip("/").replace("/", "_"))

    def base_url(self, root_url: str) -> str:
        """The URL to fetch: `url` override if given (absolute or relative), else `path`."""
        target = self.url or self.path
        if target.startswith(("http://", "https://")):
            return target
        return f"{root_url.rstrip('/')}/{target.strip('/')}"


class Site(BaseModel):
    """A website's crawl allowlist: root URL + sections."""

    model_config = ConfigDict(extra="forbid")

    root_url: str
    sections: list[Section]

    def section(self, name: str) -> Section:
        for s in self.sections:
            if s.name == name:
                return s
        raise KeyError(f"section '{name}' not found")


def load_site(path: str | Path) -> Site:
    """Load and validate a site config from YAML, failing loudly on bad input."""
    file = Path(path)
    if not file.exists():
        raise FileNotFoundError(f"site config not found: {file}")
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
        return Site.model_validate(data)
    except Exception as e:
        raise ValueError(f"invalid site config {file}: {e}") from e
