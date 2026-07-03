"""Crawl-target configuration.

*What* to crawl is data: it lives in ``sites/*.yaml``. *How* to crawl is code.
This module only defines the typed shape of that data and knows how to load it.
See PLAN.md (Phase 1).
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, model_validator


class Topic(BaseModel):
    """One crawl unit: a page to visit plus free-text instructions for the agent."""

    # Reject unknown keys so a typo like `Important instructions:` fails loudly
    # instead of silently dropping the value.
    model_config = ConfigDict(extra="forbid")

    name: str                          # used as the output filename
    path: list[str] | None = None      # labels to click from the root page to reach `url`
    url: str | None = None             # OR an explicit (relative or absolute) URL
    subtopics: list[str] | None = None # sub-page labels on `url` to resolve + crawl too
    instructions: str = ""

    @model_validator(mode="after")
    def _need_path_or_url(self) -> "Topic":
        if not self.path and not self.url:
            raise ValueError(f"topic '{self.name}' needs either 'path' or 'url'")
        if self.subtopics and not self.url:
            raise ValueError(f"topic '{self.name}': 'subtopics' require a base 'url'")
        return self


class Site(BaseModel):
    """A website to crawl: a root URL and the list of topics under it."""

    model_config = ConfigDict(extra="forbid")

    site: str
    root_url: str
    topics: list[Topic]

    def topic(self, name: str) -> Topic:
        for t in self.topics:
            if t.name == name:
                return t
        raise KeyError(f"topic '{name}' not found in site '{self.site}'")


def load_site(path: str | Path) -> Site:
    """Load and validate a site config from YAML, failing loudly on bad input."""
    file = Path(path)
    if not file.exists():
        raise FileNotFoundError(f"site config not found: {file}")
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    return Site.model_validate(data)
