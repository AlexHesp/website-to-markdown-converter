#!/usr/bin/env python3
"""Writing crawl results out: CSV, one combined markdown file, or a folder tree."""

import csv
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from extraction import Page, sanitize_filename


def sanitize_for_csv(text: str) -> str:
    """Normalise line endings for CSV output."""
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def save_csv(pages: list[Page], output_path: Path, include_links: bool = False) -> None:
    """Save results to CSV, one row per URL."""
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)

        headers = ["url", "title", "success", "error", "markdown"]
        if include_links:
            headers.append("internal_links")
        writer.writerow(headers)

        for p in pages:
            row = [p.url, p.title, p.success, p.error, sanitize_for_csv(p.markdown)]
            if include_links:
                row.append("\n".join(p.internal_links))
            writer.writerow(row)

    print(f"📄 CSV saved to: {output_path}")


def save_combined_markdown(pages: list[Page], output_path: Path, include_links: bool = False) -> None:
    """Save every page into a single markdown file, ready to feed to an LLM."""
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Combined Crawl Results\n\n")
        f.write(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*\n\n")
        f.write(f"*Total URLs: {len(pages)}*\n\n")
        f.write("---\n\n")

        for i, p in enumerate(pages, 1):
            if p.success and p.markdown:
                f.write(f"## {i}. {p.title or p.url}\n\n")
                f.write(f"**Source:** {p.url}\n\n")
                f.write(p.markdown)

                if include_links and p.internal_links:
                    f.write("\n\n### Internal Links\n\n")
                    for link in p.internal_links[:20]:
                        f.write(f"- {link}\n")
                    if len(p.internal_links) > 20:
                        f.write(f"- ... and {len(p.internal_links) - 20} more\n")

                f.write("\n\n---\n\n")
            else:
                f.write(f"## {i}. {p.url}\n\n")
                reason = p.error or "No content extracted"
                f.write(f"*Failed to crawl: {reason}*\n\n")
                f.write("---\n\n")

    print(f"📝 Combined markdown saved to: {output_path}")


def save_links_file(pages: list[Page], output_path: Path) -> None:
    """Save every internal link discovered, de-duplicated and sorted."""
    all_links = sorted({link for p in pages for link in p.internal_links})

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Discovered Internal Links\n")
        f.write(f"# Total unique links: {len(all_links)}\n\n")
        for link in all_links:
            f.write(f"{link}\n")

    print(f"🔗 Links saved to: {output_path} ({len(all_links)} unique)")


def save_failed_urls(pages: list[Page], output_path: Path) -> int:
    """
    Write still-failing URLs to a file that can be fed straight back in.

    Formatted as a urls.txt, with each failure's reason as a comment above it,
    so a follow-up run is just `python crawler.py failed_urls_<timestamp>.txt`.
    """
    failed = [p for p in pages if not p.success]
    if not failed:
        return 0

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# URLs that failed after all retries\n")
        f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("# Re-run with: python crawler.py <this file>\n\n")
        for p in failed:
            f.write(f"# {p.error or 'Unknown error'}\n{p.url}\n")

    print(f"🔁 {len(failed)} failed URLs saved to: {output_path}")
    return len(failed)


def save_structured_files(pages: list[Page], output_dir: Path) -> None:
    """
    Save each page as its own file, in folders mirroring the URL path.

    https://example.com/foo/bar -> output/example.com/foo/Page Title.md
    """
    count = 0
    for p in pages:
        if not p.success or not p.markdown:
            continue

        try:
            parsed = urlparse(p.url)
            path = parsed.path.strip("/")
            parts = path.split("/") if path else []

            # Everything but the last path segment becomes the directory, since
            # that segment names the page itself.
            if not path:
                dir_path = output_dir / parsed.netloc
            elif p.url.endswith("/"):
                dir_path = output_dir / parsed.netloc / Path(*parts)
            else:
                dir_path = output_dir / parsed.netloc / Path(*parts[:-1])

            title = sanitize_filename(p.title) or (parts[-1] if parts else "index")
            dir_path.mkdir(parents=True, exist_ok=True)

            file_path = dir_path / f"{title}.md"
            counter = 1
            while file_path.exists():
                file_path = dir_path / f"{title}_{counter}.md"
                counter += 1

            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"# {p.title or p.url}\n\n")
                f.write(f"**Source:** {p.url}\n\n")
                f.write(p.markdown)
                if p.internal_links:
                    f.write("\n\n---\n### Internal Links\n\n")
                    for link in p.internal_links:
                        f.write(f"- {link}\n")

            count += 1

        except Exception as e:
            print(f"⚠️  Failed to save structured file for {p.url}: {e}")

    print(f"📂 Saved {count} files in structured folders under {output_dir}")
