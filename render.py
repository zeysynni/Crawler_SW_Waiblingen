"""Turn one fetched page into its clean markdown — pure, no I/O.

There are two ways to build the clean form, and every caller has to make the
same choice:

  * a section with ``extract: <name>`` builds it from the *fetched HTML*
    (``extract.EXTRACTORS``) — needed when a JS component ships its data in an
    attribute and renders only a slice of it into the DOM
  * every other page goes through ``clean.clean_markdown`` on crawl4ai's
    markdown conversion

That choice used to sit inside ``main.save_outputs``, tangled with writing
files, so a second caller (the UI) would have had to copy it. It lives here
instead: one rule, one place, and testable without a crawl.
"""

from typing import TYPE_CHECKING

from clean import clean_markdown
from extract import EXTRACTORS

if TYPE_CHECKING:                  # only for the type hints — don't import crawl4ai
    from config import Site
    from crawl import PageResult



def render_clean(page: "PageResult", site: "Site") -> str:
    """Clean markdown for one successfully fetched page.

    `site.stop_at` supplies the regexes that mark where the site's noise
    begins; an `extract:` section ignores them, because its extractor builds
    the markdown from the HTML itself and never sees the page's tail.

    A broken extractor raises (a site relaunch changes the HTML and the
    component is gone). Raising is deliberate: the caller decides what it
    means — ``main.save_outputs`` marks the page failed and keeps the run
    going, the UI shows the message to the person who typed the config.
    """
    if page.extract:
        return EXTRACTORS[page.extract](page.html, page.url)
    return clean_markdown(page.raw_markdown, page.url, site.stop_at)
