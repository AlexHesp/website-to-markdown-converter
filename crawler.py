#!/usr/bin/env python3
"""
Multi-URL Crawler using Crawl4AI
Reads URLs from a file, crawls them efficiently, and outputs:
1. A CSV with URL and markdown content
2. A combined .md file with all content
3. Optionally extracts internal links
"""

import asyncio
import csv
import sys
import argparse
import re
from pathlib import Path

# Force UTF-8 output for Windows consoles
sys.stdout.reconfigure(encoding='utf-8')
from datetime import datetime
from urllib.parse import urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator


# Default CSS selectors to exclude (nav, footers, ads, etc.)
DEFAULT_EXCLUDED_SELECTORS = [
    # Structural elements
    "header",
    "footer",
    "nav",
    "aside",
    ".header",
    ".footer",
    ".nav",
    ".sidebar",
    ".navigation",
    ".menu",
    # Ads
    ".ads",
    ".advertisement",
    # Social / comments
    ".social-share",
    ".comments",
    ".comment",
    ".related-posts",
    # Inline elements handled by excluded_tags but listed here for selector-based removal too
    "script",
    "style",
    "iframe",
    "noscript",
    "form",
    # Add more specific selectors here
    ".blog-sidebar",
    ".gform-subscribe",
]

# Default CSS selectors for main content extraction (tried in order; first match wins)
DEFAULT_MAIN_CONTENT_SELECTORS = [
    "main",
    "article",
    "[role='main']",
    ".main-content",
    "#main-content",
    ".content",
    "#content",
    ".post-content",
    ".entry-content",
    ".article-content",
]


def load_urls(filepath: str) -> list[str]:
    """Load URLs from a file, one per line. Ignores empty lines and comments (#)."""
    urls = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def load_excluded_selectors(filepath: str | None) -> list[str]:
    """Load custom CSS selectors to exclude from a file."""
    if not filepath:
        return DEFAULT_EXCLUDED_SELECTORS
    
    selectors = []
    path = Path(filepath)
    if path.exists():
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    selectors.append(line)
        print(f"📋 Loaded {len(selectors)} custom exclusion selectors")
        return selectors
    else:
        print(f"⚠️  Exclusion file not found: {filepath}, using defaults")
        return DEFAULT_EXCLUDED_SELECTORS


def sanitize_for_csv(text: str) -> str:
    """Clean text for CSV output."""
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def extract_internal_links(result, base_url: str) -> list[str]:
    """Extract internal links from crawl result."""
    if not result.links:
        return []
    
    base_domain = urlparse(base_url).netloc
    internal = []
    
    # result.links has 'internal' and 'external' keys
    if isinstance(result.links, dict):
        for link_info in result.links.get("internal", []):
            if isinstance(link_info, dict):
                internal.append(link_info.get("href", ""))
            else:
                internal.append(str(link_info))
    
    return [l for l in internal if l]


async def crawl_urls(
    urls: list[str],
    use_js: bool = False,
    extract_links: bool = False,
    excluded_selectors: list[str] = None,
    main_content_selectors: list[str] = None,
    filter_threshold: float = 0.48,
) -> list[dict]:
    """
    Crawl all URLs in parallel and return results.

    Args:
        urls: List of URLs to crawl
        use_js: Whether to enable JavaScript rendering (slower but needed for dynamic sites)
        extract_links: Whether to extract internal links from pages
        excluded_selectors: CSS selectors to exclude from content
        main_content_selectors: CSS selectors for main content extraction (comma-joined, first match wins)
        filter_threshold: PruningContentFilter threshold (lower = more content)
    """
    results = []
    excluded = excluded_selectors or DEFAULT_EXCLUDED_SELECTORS
    main_selectors = main_content_selectors if main_content_selectors is not None else DEFAULT_MAIN_CONTENT_SELECTORS

    # Configure browser
    browser_config = BrowserConfig(
        headless=True,
        java_script_enabled=use_js,
    )

    # Configure markdown generation with content filtering
    md_generator = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(
            threshold=filter_threshold,
            threshold_type="fixed"
        )
    )

    # Configure crawler run
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        markdown_generator=md_generator,
        excluded_tags=["script", "style", "noscript", "iframe", "form"],
        excluded_selector=",".join(excluded),  # CSS selectors to remove
        css_selector=",".join(main_selectors) if main_selectors else None,  # target main content
        page_timeout=30000,
        stream=True,
    )
    
    mode_str = "JS-rendered" if use_js else "static HTML"
    print(f"\n🚀 Starting crawl of {len(urls)} URLs ({mode_str})...\n")
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        async for result in await crawler.arun_many(urls, config=run_config):
            if result.success:
                # Get filtered markdown
                markdown = ""
                if result.markdown:
                    if hasattr(result.markdown, 'fit_markdown') and result.markdown.fit_markdown:
                        markdown = result.markdown.fit_markdown
                    elif hasattr(result.markdown, 'raw_markdown'):
                        markdown = result.markdown.raw_markdown
                    else:
                        markdown = str(result.markdown)
                
                # Strip links from markdown content if link extraction is not enabled
                if not extract_links:
                    # Replace [text](url) with text, but keep ![image](url)
                    markdown = re.sub(r'(?<!!)\[([^\]]+)\]\([^\)]+\)', r'\1', markdown)
                
                entry = {
                    "url": result.url,
                    "success": True,
                    "markdown": markdown,
                    "title": result.metadata.get("title", "") if result.metadata else "",
                }
                
                if extract_links:
                    entry["internal_links"] = extract_internal_links(result, result.url)
                
                results.append(entry)
                link_count = len(entry.get("internal_links", [])) if extract_links else 0
                link_info = f", {link_count} links" if extract_links else ""
                print(f"✅ {result.url} ({len(markdown):,} chars{link_info})")
            else:
                results.append({
                    "url": result.url,
                    "success": False,
                    "markdown": "",
                    "title": "",
                    "error": result.error_message,
                })
                print(f"❌ {result.url} - Error: {result.error_message}")
    
    return results


def save_csv(results: list[dict], output_path: Path, include_links: bool = False):
    """Save results to CSV."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        
        headers = ["url", "title", "success", "markdown"]
        if include_links:
            headers.append("internal_links")
        writer.writerow(headers)
        
        for r in results:
            row = [
                r["url"],
                r.get("title", ""),
                r["success"],
                sanitize_for_csv(r["markdown"]),
            ]
            if include_links:
                links = r.get("internal_links", [])
                row.append("\n".join(links) if links else "")
            writer.writerow(row)
    
    print(f"\n📄 CSV saved to: {output_path}")


def save_combined_markdown(results: list[dict], output_path: Path, include_links: bool = False):
    """Save all content to a single combined markdown file."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Combined Crawl Results\n\n")
        f.write(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        f.write(f"*Total URLs: {len(results)}*\n\n")
        f.write("---\n\n")
        
        for i, r in enumerate(results, 1):
            if r["success"] and r["markdown"]:
                title = r.get("title") or r["url"]
                f.write(f"## {i}. {title}\n\n")
                f.write(f"**Source:** {r['url']}\n\n")
                f.write(r["markdown"])
                
                if include_links and r.get("internal_links"):
                    f.write("\n\n### Internal Links\n\n")
                    for link in r["internal_links"][:20]:  # Limit to first 20
                        f.write(f"- {link}\n")
                    if len(r["internal_links"]) > 20:
                        f.write(f"- ... and {len(r['internal_links']) - 20} more\n")
                
                f.write("\n\n---\n\n")
            else:
                f.write(f"## {i}. {r['url']}\n\n")
                f.write(f"*Failed to crawl: {r.get('error', 'Unknown error')}*\n\n")
                f.write("---\n\n")
    
    print(f"📝 Combined markdown saved to: {output_path}")


def save_links_file(results: list[dict], output_path: Path):
    """Save all discovered internal links to a separate file."""
    all_links = set()
    for r in results:
        if r.get("internal_links"):
            all_links.update(r["internal_links"])
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Discovered Internal Links\n")
        f.write(f"# Total unique links: {len(all_links)}\n\n")
        for link in sorted(all_links):
            f.write(f"{link}\n")
    
    print(f"🔗 Links saved to: {output_path} ({len(all_links)} unique)")


def sanitize_filename(name: str) -> str:
    """Sanitize string to be safe for filenames."""
    # Remove invalid characters
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    # Replace newlines and tabs
    name = name.replace('\n', ' ').replace('\r', '').replace('\t', ' ')
    # Trim whitespace
    return name.strip()


def save_structured_files(results: list[dict], output_dir: Path):
    """
    Save content in a folder structure matching the URL hierarchy.
    Filenames are based on the page title.
    Example: https://example.com/foo/bar -> output/example.com/foo/Page Title.md
    """
    count = 0
    for r in results:
        if not r["success"] or not r["markdown"]:
            continue
            
        try:
            parsed = urlparse(r["url"])
            domain = parsed.netloc
            path = parsed.path.strip("/")
            
            # Determine directory path based on URL
            if not path:
                dir_path = output_dir / domain
            else:
                parts = path.split("/")
                if r["url"].endswith("/"):
                     dir_path = output_dir / domain / path
                else:
                     # If it doesn't end in slash, the last part is usually a file or resource name.
                     # We want to keep the structure, so we use the parent of the resource as the folder.
                     # e.g. example.com/foo/bar -> output/example.com/foo/
                     parent_path = Path(*parts[:-1])
                     dir_path = output_dir / domain / parent_path
            
            # Determine filename from title
            title = r.get("title", "").strip()
            if not title:
                # Fallback to last segment of path or index
                title = parts[-1] if path and not r["url"].endswith("/") else "index"
            
            filename = f"{sanitize_filename(title)}.md"
            
            # Create directories
            dir_path.mkdir(parents=True, exist_ok=True)
            
            # Handle collisions
            file_path = dir_path / filename
            counter = 1
            while file_path.exists():
                file_path = dir_path / f"{sanitize_filename(title)}_{counter}.md"
                counter += 1
            
            # Write content
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n")
                f.write(f"**Source:** {r['url']}\n\n")
                f.write(r["markdown"])
                
                if r.get("internal_links"):
                    f.write("\n\n---\n### Internal Links\n\n")
                    for link in r["internal_links"]:
                        f.write(f"- {link}\n")
            
            count += 1
            
        except Exception as e:
            print(f"⚠️  Failed to save structured file for {r['url']}: {e}")
            
    print(f"📂 Saved {count} files in structured folders under {output_dir}")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Crawl multiple URLs and output clean markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python crawler.py                          # Basic crawl (no JS)
  python crawler.py --js                     # Enable JavaScript rendering
  python crawler.py --links                  # Extract internal links
  python crawler.py --exclude exclude.txt    # Custom CSS exclusions
  python crawler.py --threshold 0.3          # Lower threshold = more content
        """
    )
    parser.add_argument("urls_file", nargs="?", default="urls.txt",
                        help="File containing URLs to crawl (default: urls.txt)")
    parser.add_argument("-o", "--output", default="output",
                        help="Output directory (default: output)")
    parser.add_argument("--js", action="store_true",
                        help="Enable JavaScript rendering (slower, use for dynamic sites)")
    parser.add_argument("--links", action="store_true",
                        help="Extract internal links from pages")
    parser.add_argument("--exclude", metavar="FILE",
                        help="File with CSS selectors to exclude (one per line)")
    parser.add_argument("--threshold", type=float, default=0.48,
                        help="Content filter threshold 0.0-1.0 (default: 0.48, lower=more content)")
    parser.add_argument("--no-default-exclusions", action="store_true",
                        help="Don't use default CSS exclusions (nav, footer, ads, etc.)")
    parser.add_argument("--no-main-selector", action="store_true",
                        help="Don't restrict extraction to main content selectors (use full page)")
    parser.add_argument("--structured", action="store_true",
                        help="Save output in a folder structure matching URL paths")
    
    return parser.parse_args()


async def main():
    args = parse_args()
    
    urls_file = Path(args.urls_file)
    output_dir = Path(args.output)
    
    # Validate input file
    if not urls_file.exists():
        print(f"❌ URLs file not found: {urls_file}")
        print("\nCreate a urls.txt file with one URL per line.")
        sys.exit(1)
    
    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Load URLs
    urls = load_urls(urls_file)
    if not urls:
        print("❌ No URLs found in file")
        sys.exit(1)
    
    print(f"📋 Loaded {len(urls)} URLs from {urls_file}")
    
    # Load exclusion selectors
    if args.no_default_exclusions:
        excluded = []
        if args.exclude:
            excluded = load_excluded_selectors(args.exclude)
    else:
        excluded = load_excluded_selectors(args.exclude) if args.exclude else DEFAULT_EXCLUDED_SELECTORS
    
    print(f"🚫 Excluding {len(excluded)} CSS selectors")

    # Main content selectors
    main_selectors = [] if args.no_main_selector else DEFAULT_MAIN_CONTENT_SELECTORS
    if main_selectors:
        print(f"🎯 Targeting main content via {len(main_selectors)} selectors")

    # Crawl
    results = await crawl_urls(
        urls,
        use_js=args.js,
        extract_links=args.links,
        excluded_selectors=excluded,
        main_content_selectors=main_selectors,
        filter_threshold=args.threshold,
    )
    
    # Generate timestamp for output files
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Save outputs
    csv_path = output_dir / f"crawl_results_{timestamp}.csv"
    md_path = output_dir / f"combined_content_{timestamp}.md"
    
    save_csv(results, csv_path, include_links=args.links)
    save_combined_markdown(results, md_path, include_links=args.links)
    
    # Save structured files if requested
    if args.structured:
        save_structured_files(results, output_dir)
    
    # Save links file if extracting links
    if args.links:
        links_path = output_dir / f"discovered_links_{timestamp}.txt"
        save_links_file(results, links_path)
    
    # Summary
    successful = sum(1 for r in results if r["success"])
    print(f"\n✨ Done! {successful}/{len(results)} URLs crawled successfully")


if __name__ == "__main__":
    asyncio.run(main())
