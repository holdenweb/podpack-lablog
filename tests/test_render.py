"""What a stored entry becomes on the page.

The sample is the one measured against Python-Markdown 3.10.3 while choosing a
renderer: a list straight after a paragraph line, raw HTML, and a `javascript:`
link. That library ran the list into the paragraph, passed the `<script>`
through and made the link live. Swapping it back in turns this file red.
"""

from flask.testing import FlaskClient

from conftest import add_entry, make_project, start_experiment

from podpack_lablog.render import markdown

SAMPLE = """Results:
- first
  - nested two-space
- second

<script>alert(1)</script>

[x](javascript:alert(1)) and [ok](https://example.com)

```python
print("<hi>")
```
"""


def test_a_list_after_a_paragraph_line_is_a_list() -> None:
    html = markdown(SAMPLE)
    assert "<p>Results:</p>\n<ul>\n<li>first\n<ul>\n<li>nested two-space</li>" in html


def test_raw_html_is_shown_not_run() -> None:
    html = markdown(SAMPLE)
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_a_javascript_link_is_not_a_link() -> None:
    html = markdown(SAMPLE)
    assert 'href="javascript:' not in html
    assert '<a href="https://example.com">ok</a>' in html


def test_code_blocks_keep_their_text() -> None:
    assert '<pre><code class="language-python">print(&quot;&lt;hi&gt;&quot;)' in markdown(SAMPLE)


def test_tables_and_strikethrough_render() -> None:
    html = markdown("| a | b |\n|---|---|\n| 1 | 2 |\n\n~~gone~~")
    assert "<table>" in html and "<s>gone</s>" in html


def test_an_entry_on_the_page_is_escaped_too(holder: FlaskClient) -> None:
    """Through the template, where an extra `|safe` or a missed escape would bite."""
    slug = make_project(holder)
    start_experiment(holder, slug, expectation="<img src=x onerror=alert(1)>")
    add_entry(holder, slug, 1, body=SAMPLE, kind="claude")
    body = holder.get(f"/lablog/{slug}/").get_data(as_text=True)
    assert "<script>alert(1)" not in body
    assert "<img src=x" not in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body
