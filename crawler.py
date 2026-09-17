#!/usr/bin/env python3
"""
Multi-URL Crawler
Reads URLs from a file, fetches them concurrently, and outputs:
1. A CSV with URL and markdown content
2. A combined .md file with all content
3. Optionally, structured folders and extracted internal links

Fetching is plain HTTP (no browser), and content detection is handled by
trafilatura, which finds the main article body and drops boilerplate itself.
"""

import argparse
import asyncio
import sys
import time
from datetime import datetime
from pathlib import Path

# Force UTF-8 output for Windows consoles
sys.stdout.reconfigure(encoding="utf-8")

from extraction import EXTRACTION_MODES, Page, extract_page
from fetching import DEFAULT_CONCURRENCY, Fetcher, FetchProgress
from outputs import (
    save_combined_markdown,
    save_csv,
    save_failed_urls,
    save_links_file,
    save_structured_files,
)
from proxy import ProxyConfigError, build_proxy_endpoints, check_proxies, load_proxy_settings


# Blocks that repeat across a site and are not page content. trafilatura already
# removes headers, footers and navigation, so this list is for the site-specific
# extras it cannot know about - related-content carousels, subscribe forms, and
# so on. Add your own site's selectors here.
DEFAULT_EXCLUDED_SELECTORS = [
    ".js-cards",           # hso.com: "discover more" / related-content carousels
    ".gform-subscribe",
    ".blog-sidebar",
    ".social-share",
    ".related-posts",
    ".comments",
    ".comment",
    ".ads",
    ".advertisement",
    ".cookie-banner",
]

# Below this many characters a page is treated as having no real content
MIN_CONTENT_CHARS = 50


def load_urls(filepath: str | Path) -> list[str]:
    """Load URLs from a file, one per line. Ignores empty lines and comments (#)."""
    urls = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return list(dict.fromkeys(urls))  # de-duplicate, preserve order


def load_excluded_selectors(filepath: str | None, use_defaults: bool = True) -> list[str]:
    """Load CSS selectors to exclude, from a file and/or the built-in defaults."""
    selectors = list(DEFAULT_EXCLUDED_SELECTORS) if use_defaults else []

    if filepath:
        path = Path(filepath)
        if path.exists():
            custom = []
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        custom.append(line)
            print(f"📋 Loaded {len(custom)} custom exclusion selectors from {path}")
            selectors.extend(custom)
        else:
            print(f"⚠️  Exclusion file not found: {filepath}")

    return list(dict.fromkeys(selectors))


async def crawl(
    urls: list[str],
    proxy_endpoints=None,
    excluded_selectors: list[str] | None = None,
    extract_links: bool = False,
    mode: str = "balanced",
    concurrency: int = DEFAULT_CONCURRENCY,
    timeout: float = 30.0,
    retries: int = 2,
) -> list[Page]:
    """
    Fetch and extract every URL, returning results in the order given.

    Extraction runs in a worker thread as each response arrives, so parsing
    overlaps with the requests still in flight.
    """
    progress = FetchProgress(total=len(urls))
    pages: dict[str, Page] = {}

    async def handle(result) -> None:
        if result.ok:
            page = await asyncio.to_thread(
                extract_page,
                result.html,
                result.url,
                excluded_selectors,
                extract_links,
                mode,
            )
            detail = f"({len(page.markdown):,} chars"
            detail += f", {len(page.internal_links)} links)" if extract_links else ")"
            if result.attempts > 1:
                detail += f" after {result.attempts} attempts"
        else:
            page = Page(url=result.url, success=False, error=result.error)
            detail = f"- {result.error}"

        pages[result.url] = page
        progress.record(page.success, result.url, detail)

    async with Fetcher(
        endpoints=proxy_endpoints,
        concurrency=concurrency,
        timeout=timeout,
        retries=retries,
    ) as fetcher:
        await fetcher.fetch_all(urls, on_result=handle)

    print(f"\n   {progress.summary()}")

    # Preserve the caller's order, and never silently drop a URL
    return [
        pages.get(url) or Page(url=url, success=False, error="No result returned")
        for url in urls
    ]


async def resolve_proxy(args):
    """
    Turn CLI flags plus .env into proxy endpoints, printing what was resolved.

    Returns None when crawling directly. Exits on a configuration error, or on a
    failed health check when --check-proxy was requested.
    """
    # --check-proxy implies the proxy should be on
    enabled = True if args.check_proxy and args.proxy is None else args.proxy

    try:
        settings = load_proxy_settings(
            env_file=args.env_file,
            enabled=enabled,
            sessions=args.proxy_sessions,
            country=args.proxy_country,
        )
    except ProxyConfigError as e:
        print(f"❌ Proxy configuration error: {e}")
        sys.exit(1)

    if settings is None:
        if args.check_proxy:
            print("❌ --check-proxy needs the proxy enabled (use --proxy or set PROXY_ENABLED=true)")
            sys.exit(1)
        print("🌐 Proxy disabled - crawling directly")
        return None

    endpoints = build_proxy_endpoints(settings)
    print(f"🛡️  Proxy enabled: {settings.describe()}")

    if args.check_proxy:
        print("\n🔎 Checking proxy connectivity...")
        checks = await check_proxies(endpoints)
        for check in checks:
            if check.ok:
                print(f"  ✅ {check.endpoint.label}: exit IP {check.exit_ip}")
            else:
                print(f"  ❌ {check.endpoint.label}: {check.error}")
        if not any(c.ok for c in checks):
            print("\n❌ No proxy session could be reached. Check your credentials and plan status.")
            sys.exit(1)
        unique = {c.exit_ip for c in checks if c.ok}
        print(f"\n✨ {sum(c.ok for c in checks)}/{len(checks)} sessions OK, {len(unique)} unique exit IP(s)")
        sys.exit(0)

    return endpoints


def parse_args():
    parser = argparse.ArgumentParser(
        description="Crawl multiple URLs and output clean markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python crawler.py                          # Crawl urls.txt
  python crawler.py --links                  # Extract internal links
  python crawler.py --structured             # Save a folder per URL path
  python crawler.py --mode precision         # Keep less borderline content
  python crawler.py --concurrency 80         # Fetch more pages at once

Proxy (ProxyScrape) examples:
  python crawler.py --proxy                  # Force proxy on (credentials from .env)
  python crawler.py --no-proxy               # Force proxy off
  python crawler.py --proxy-sessions 5       # Spread requests over 5 sticky IPs
  python crawler.py --check-proxy            # Test the proxy, print exit IPs, exit
        """,
    )
    parser.add_argument("urls_file", nargs="?", default="urls.txt",
                        help="File containing URLs to crawl (default: urls.txt)")
    parser.add_argument("-o", "--output", default="output",
                        help="Output directory (default: output)")
    parser.add_argument("--links", action="store_true",
                        help="Keep inline links and extract internal links from pages")
    parser.add_argument("--structured", action="store_true",
                        help="Save output in a folder structure matching URL paths")
    parser.add_argument("--mode", choices=EXTRACTION_MODES, default="balanced",
                        help="How much borderline content to keep (default: balanced)")
    parser.add_argument("--exclude", metavar="FILE",
                        help="File with extra CSS selectors to exclude (one per line)")
    parser.add_argument("--no-default-exclusions", action="store_true",
                        help="Don't use the built-in CSS exclusions")

    perf = parser.add_argument_group("performance and reliability")
    perf.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY, metavar="N",
                      help=f"Pages to fetch at once (default: {DEFAULT_CONCURRENCY})")
    perf.add_argument("--timeout", type=float, default=30.0, metavar="SECONDS",
                      help="Per-request timeout (default: 30)")
    perf.add_argument("--retries", type=int, default=2, metavar="N",
                      help="Retries per URL after the first attempt (default: 2)")

    proxy_group = parser.add_argument_group("proxy (ProxyScrape)")
    toggle = proxy_group.add_mutually_exclusive_group()
    toggle.add_argument("--proxy", dest="proxy", action="store_true", default=None,
                        help="Route traffic through the proxy (overrides PROXY_ENABLED)")
    toggle.add_argument("--no-proxy", dest="proxy", action="store_false",
                        help="Crawl directly, ignoring PROXY_ENABLED")
    proxy_group.add_argument("--proxy-sessions", type=int, metavar="N",
                             help="Number of sticky proxy sessions to spread requests over")
    proxy_group.add_argument("--proxy-country", metavar="CODE",
                             help="Country code for proxy exit nodes (e.g. us, gb, de)")
    proxy_group.add_argument("--check-proxy", action="store_true",
                             help="Test the proxy connection, print exit IPs, and exit")
    proxy_group.add_argument("--env-file", metavar="FILE", default=".env",
                             help="Path to the .env file with proxy credentials (default: .env)")

    return parser.parse_args()


async def main():
    args = parse_args()

    # Resolve the proxy first: --check-proxy exits here and needs no URLs file
    proxy_endpoints = await resolve_proxy(args)

    urls_file = Path(args.urls_file)
    output_dir = Path(args.output)

    if not urls_file.exists():
        print(f"❌ URLs file not found: {urls_file}")
        print("\nCreate a urls.txt file with one URL per line.")
        sys.exit(1)

    urls = load_urls(urls_file)
    if not urls:
        print("❌ No URLs found in file")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"📋 Loaded {len(urls)} URLs from {urls_file}")

    excluded = load_excluded_selectors(args.exclude, use_defaults=not args.no_default_exclusions)
    print(f"🚫 Excluding {len(excluded)} CSS selectors")

    route = "via proxy" if proxy_endpoints else "direct"
    print(f"\n🚀 Crawling {len(urls)} URLs - {route}, {args.concurrency} at a time, mode: {args.mode}\n")

    started = time.perf_counter()
    pages = await crawl(
        urls,
        proxy_endpoints=proxy_endpoints,
        excluded_selectors=excluded,
        extract_links=args.links,
        mode=args.mode,
        concurrency=args.concurrency,
        timeout=args.timeout,
        retries=args.retries,
    )
    elapsed = time.perf_counter() - started
    print(f"⏱️  Total time: {elapsed // 60:.0f}m{elapsed % 60:04.1f}s")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_csv(pages, output_dir / f"crawl_results_{timestamp}.csv", include_links=args.links)
    save_combined_markdown(pages, output_dir / f"combined_content_{timestamp}.md", include_links=args.links)

    if args.structured:
        save_structured_files(pages, output_dir)
    if args.links:
        save_links_file(pages, output_dir / f"discovered_links_{timestamp}.txt")

    failed_count = save_failed_urls(pages, output_dir / f"failed_urls_{timestamp}.txt")

    successful = sum(1 for p in pages if p.success)
    thin = sum(1 for p in pages if p.success and len(p.markdown) < MIN_CONTENT_CHARS)

    print(f"\n✨ Done! {successful}/{len(pages)} URLs crawled successfully")
    if thin:
        print(f"ℹ️  {thin} pages had almost no text (thin pages, or a selector miss)")
    if failed_count:
        print(f"⚠️  {failed_count} URLs still failed; re-run them with:")
        print(f"     python crawler.py {output_dir / f'failed_urls_{timestamp}.txt'}")


if __name__ == "__main__":
    asyncio.run(main())
