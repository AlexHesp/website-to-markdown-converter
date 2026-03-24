# Website to Markdown Converter

A fast, parallel web crawler that extracts clean markdown content from any list of URLs. Built for creating datasets from websites for documentation, analysis, or LLM knowledge bases.

## Features

- **Parallel crawling** — Crawls all URLs concurrently for speed
- **Clean markdown output** — Strips navigation, footers, ads, and boilerplate automatically
- **Smart content targeting** — Focuses on main content areas and filters out noise
- **Link extraction** — Optionally extract all internal links from pages
- **Multiple output formats**:
  - Combined `.md` file with all content in one document
  - CSV with URL, title, and markdown columns
  - Structured folders mirroring URL paths with files named by page title

## Installation

```bash
pip install -r requirements.txt
crawl4ai-setup
```

If you have browser issues:
```bash
python -m playwright install --with-deps chromium
```

## Quick Start

1. **Get your URLs.** A sitemap extractor like [SEOwl Sitemap Extractor](https://www.seowl.co/sitemap-extractor/) is a great way to pull all URLs from a website. Once you have the list, remove any irrelevant pages (e.g. tag pages, author archives, terms, etc.) to keep your output focused.

2. **Add your URLs** to `urls.txt`, one per line:
   ```txt
   # Lines starting with # are ignored
   https://example.com/page-1
   https://example.com/page-2
   ```

3. **Run the crawler:**
   ```bash
   python crawler.py
   ```

4. **Check the `output/` directory** for your results.

## Usage

```bash
# Basic crawl (fast, static HTML — recommended for most sites)
python crawler.py

# Extract internal links from each page
python crawler.py --links

# Save structured files (folders match URL hierarchy, files named by page title)
python crawler.py --structured

# Custom URL file and output directory
python crawler.py my_urls.txt -o results/

# Combine options
python crawler.py --links --structured
```

## Options

| Flag | Description |
|------|-------------|
| `--js` | Enable JavaScript rendering (see warning below) |
| `--links` | Extract internal links from each page |
| `--structured` | Save output in folders matching URL paths, named by page title |
| `--exclude FILE` | File with additional CSS selectors to exclude (one per line) |
| `--threshold 0.48` | Content filter strictness 0.0–1.0 (lower = more content) |
| `--no-default-exclusions` | Disable built-in CSS exclusions |
| `--no-main-selector` | Don't restrict extraction to main content selectors (use full page) |
| `-o DIR` | Output directory (default: `output`) |

## Output

After crawling, your `output/` directory will look like this:

```
output/
├── crawl_results_20260324_143022.csv        # CSV with all data
├── combined_content_20260324_143022.md      # All pages in one file
├── discovered_links_20260324_143022.txt     # (only with --links)
└── example.com/                             # (only with --structured)
    ├── Page Title.md
    ├── about/
    │   └── Our Story.md
    └── blog/
        ├── First Post.md
        └── Second Post.md
```

### Combined Markdown
A single file containing all crawled pages, separated by headers. Each page includes its title, source URL, and the extracted content. Ideal for uploading to an LLM as a knowledge base.

### CSV Report
Structured data file with columns: URL, Title, Success, Markdown (+ Internal Links if using `--links`). Useful for data analysis or programmatic processing.

### Structured Folders
Recreates the website's URL hierarchy locally. Each page is saved as its own `.md` file named after the page title. Great for browsing content offline.

## JavaScript Rendering

**JavaScript rendering (`--js`) is generally not recommended**, especially for large websites. It is significantly slower, uses more memory, and is more prone to timeouts. Most websites serve their content as static HTML and don't need it.

Only use `--js` if your target site is a single-page application (React, Vue, Angular) or loads content dynamically via AJAX. Test with a few URLs first before running it on a large list.

## Customising Exclusions and Selectors

The crawler uses two sets of CSS selectors to extract clean content:

1. **Excluded selectors** — Elements to remove (nav, footer, ads, sidebars, etc.)
2. **Main content selectors** — Elements to target as the main content area

Both are defined as lists at the top of `crawler.py` (`DEFAULT_EXCLUDED_SELECTORS` and `DEFAULT_MAIN_CONTENT_SELECTORS`). The recommended approach is to **edit these lists directly in the code** for your use case, rather than using external files. This keeps your configuration in one place and avoids passing extra flags on every run.

For example, if a site has a specific element you want to exclude, add it to `DEFAULT_EXCLUDED_SELECTORS`:

```python
DEFAULT_EXCLUDED_SELECTORS = [
    # ... existing selectors ...
    ".cookie-banner",
    "#newsletter-modal",
]
```

Or if the site uses a non-standard content wrapper, add it to `DEFAULT_MAIN_CONTENT_SELECTORS`:

```python
DEFAULT_MAIN_CONTENT_SELECTORS = [
    # ... existing selectors ...
    ".custom-content-wrapper",
]
```

Try runing this on various page types to see how well it works. You may need to adjust the selectors to get the best results for your specific use case.


The `--exclude` flag is still available if you prefer to keep exclusions in a separate file, and `--no-default-exclusions` / `--no-main-selector` can disable the defaults entirely.

## Content Filter Threshold

Adjust how aggressively the crawler filters content:

```bash
python crawler.py --threshold 0.3   # Keep more content (may include some noise)
python crawler.py --threshold 0.6   # Stricter filtering (may miss some content)
```

- **0.3–0.4:** Keeps more content, may include some noise
- **0.48** (default): Balanced
- **0.5–0.7:** Stricter, may miss some content

## License

[MIT](LICENSE)
