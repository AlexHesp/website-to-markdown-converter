#!/usr/bin/env python3
"""
Turning fetched HTML into clean markdown.

trafilatura does the content detection: it finds the main article body and
drops navigation, footers and other boilerplate on its own, so there is no list
of "main content" selectors to maintain. What it cannot know is which blocks a
particular site repeats on every page - related-content carousels, subscribe
forms - so those are removed by CSS selector first.
"""

import copy
import re
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import urljoin, urlparse

import lxml.html
import trafilatura
from cssselect import GenericTranslator, SelectorError

_TRANSLATOR = GenericTranslator()

# How hard to try to keep borderline content
EXTRACTION_MODES = ("balanced", "recall", "precision")


@dataclass
class Page:
    """Extracted content for a single URL."""

    url: str
    success: bool = True
    title: str = ""
    markdown: str = ""
    internal_links: list[str] = field(default_factory=list)
    error: str = ""


@lru_cache(maxsize=256)
def _css_to_xpath(selector: str) -> str | None:
    """Translate one CSS selector to XPath, ignoring ones lxml cannot express."""
    try:
        return _TRANSLATOR.css_to_xpath(selector)
    except SelectorError:
        return None


def selectors_to_xpath(selectors: list[str]) -> list[str]:
    """Translate CSS selectors to the XPath list trafilatura prunes with."""
    xpaths = [_css_to_xpath(s) for s in selectors]
    return [x for x in xpaths if x]


def _page_title(tree) -> str:
    """
    Read the title straight off the parsed tree.

    trafilatura's extract_metadata() would also find this, but it costs ~180ms
    per page - more than the extraction itself - because it additionally hunts
    for authors, dates and licences that this crawler never uses.
    """
    for xpath in ('//meta[@property="og:title"]/@content',
                  '//meta[@name="twitter:title"]/@content',
                  '//title/text()',
                  '//h1//text()'):
        values = tree.xpath(xpath)
        if values:
            title = " ".join(str(values[0]).split()).strip()
            if title:
                return title
    return ""


def _internal_links(tree, base_url: str) -> list[str]:
    """Same-domain links on the page, absolute and de-duplicated."""
    base_domain = urlparse(base_url).netloc
    seen: dict[str, None] = {}
    for href in tree.xpath("//a/@href"):
        href = str(href).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urljoin(base_url, href)
        if urlparse(absolute).netloc == base_domain:
            seen.setdefault(absolute.split("#")[0], None)
    return list(seen)


def extract_page(
    html: str,
    url: str,
    exclude_selectors: list[str] | None = None,
    include_links: bool = False,
    mode: str = "balanced",
) -> Page:
    """
    Extract markdown, title and (optionally) internal links from a page.

    Args:
        html: The fetched HTML
        url: The URL it came from, used for resolving relative links
        exclude_selectors: CSS selectors for blocks to drop before extraction
        include_links: Keep inline links in the markdown and collect internal links
        mode: "balanced" (default), "recall" (keep more), or "precision" (keep less)
    """
    if not html or not html.strip():
        return Page(url=url, success=False, error="Empty response body")

    try:
        tree = lxml.html.fromstring(html)
    except Exception as exc:
        return Page(url=url, success=False, error=f"Could not parse HTML: {exc}")

    # Read title and links before extraction, which consumes the tree.
    title = _page_title(tree)
    links = _internal_links(tree, url) if include_links else []

    prune = selectors_to_xpath(exclude_selectors or [])
    markdown = trafilatura.extract(
        copy.deepcopy(tree),   # trafilatura prunes the tree it is given
        output_format="markdown",
        include_links=include_links,
        include_tables=True,
        include_comments=False,
        favor_recall=(mode == "recall"),
        favor_precision=(mode == "precision"),
        prune_xpath=prune or None,
    ) or ""

    return Page(
        url=url,
        success=True,
        title=title,
        markdown=markdown.strip(),
        internal_links=links,
    )


def sanitize_filename(name: str) -> str:
    """Sanitize a string so it is safe to use as a filename."""
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    name = name.replace("\n", " ").replace("\r", "").replace("\t", " ")
    name = re.sub(r"\s+", " ", name).strip()
    return name[:150]  # keep well clear of filesystem name limits
