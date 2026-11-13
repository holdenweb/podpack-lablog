"""Turning stored text into something to read.

Markdown through markdown-it-py rather than the `markdown` library podpack-pages
uses, and the reason is the input. Pages renders trusted files from a
repository; this renders what several people type and what they paste from
Claude. Measured on a Claude-style sample, Python-Markdown 3.10.3 ran a list
that follows a paragraph line into the paragraph, passed `<script>` through
untouched and made `javascript:` links live. markdown-it-py is CommonMark, and
with `html` off it escapes raw HTML and refuses unsafe link schemes, so what
reaches the page is only what Markdown itself can express. tests/test_render.py
holds it to that.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from markdown_it import MarkdownIt
from markupsafe import Markup

# Built once: the parser's configuration is fixed, and `render` keeps its state
# per call rather than on the instance.
_md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


def markdown(text: str | None) -> Markup:
    """HTML for `text`, safe to put in a page as it stands."""
    return Markup(_md.render(text or ""))


def stamp(moment: datetime, zone: ZoneInfo) -> str:
    """A timestamp as a reader in this site's zone would write it.

    Stored values are UTC. SQLite drops tzinfo on the way back out, so a naive
    value is UTC by construction, never local time.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone).strftime("%Y-%m-%d %H:%M %Z")
