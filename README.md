# Website to Markdown Converter

A fast, parallel web crawler that extracts clean markdown content from any list of URLs. Built for creating datasets from websites for documentation, analysis, or LLM knowledge bases.

## Features

- **Parallel crawling** — Fetches pages concurrently, around 19 pages/second
- **No browser required** — Plain HTTP fetching, so no Chromium install and a fraction of the bandwidth
- **Clean markdown output** — Content detection finds the article body and drops navigation, footers and boilerplate
- **Automatic retries** — Transient failures are retried with backoff, and whatever still fails is written out ready to re-run
- **Live progress** — Per-page counter with throughput and ETA
- **Proxy support** — Optional ProxyScrape residential proxies, with country targeting and sticky sessions
- **Multiple output formats**:
  - Combined `.md` file with all content in one document
  - CSV with URL, title, and markdown columns
  - Structured folders mirroring URL paths with files named by page title

## Installation

```bash
pip install -r requirements.txt
```

That is the whole setup — there is no browser to install.

To crawl through a proxy, copy the example environment file and add your credentials:
```bash
cp .env.example .env
```
See [Proxy Support](#proxy-support) below. The proxy is entirely optional; without a `.env` the crawler fetches directly.

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
# Basic crawl
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
| `--links` | Keep inline links and extract internal links from each page |
| `--structured` | Save output in folders matching URL paths, named by page title |
| `--mode {balanced,recall,precision}` | How much borderline content to keep (default: balanced) |
| `--exclude FILE` | File with extra CSS selectors to exclude (one per line) |
| `--no-default-exclusions` | Disable the built-in CSS exclusions |
| `-o DIR` | Output directory (default: `output`) |
| `--concurrency N` | Pages to fetch at once (default: 40) |
| `--timeout SECONDS` | Per-request timeout (default: 30) |
| `--retries N` | Retries per URL after the first attempt (default: 2) |
| `--proxy` / `--no-proxy` | Force the proxy on or off for this run |
| `--proxy-sessions N` | Spread requests over N sticky proxy sessions |
| `--proxy-country CODE` | Country code for proxy exit nodes (e.g. `us`, `gb`) |
| `--check-proxy` | Test the proxy, print exit IPs, and exit |
| `--env-file FILE` | Path to the `.env` holding proxy credentials (default: `.env`) |

## How Extraction Works

Content detection is handled by [trafilatura](https://trafilatura.readthedocs.io/), which analyses the page structure to find the main article body. It removes headers, footers, navigation and sidebars on its own, so there is **no list of "main content" selectors to maintain**.

What it cannot know is which blocks a particular site repeats on every page — related-content carousels, subscribe forms, cookie banners. Those are removed by CSS selector before extraction, using `DEFAULT_EXCLUDED_SELECTORS` at the top of `crawler.py`:

```python
DEFAULT_EXCLUDED_SELECTORS = [
    ".js-cards",           # hso.com: "discover more" / related-content carousels
    ".gform-subscribe",
    # ... add your own site's selectors here
]
```

Editing that list in the code is the recommended approach, since it keeps the configuration in one place. The `--exclude` flag is available if you would rather keep selectors in a separate file, and `--no-default-exclusions` turns the built-ins off.

### Tuning what gets kept

If pages come out with too much navigation cruft, or too little content, adjust the mode:

```bash
python crawler.py --mode precision   # Stricter, keeps less borderline content
python crawler.py --mode recall      # Looser, keeps more (may include some noise)
```

If a specific block keeps appearing in your output, find its CSS class in the page source and add it to `DEFAULT_EXCLUDED_SELECTORS` — that is usually more effective than changing the mode.

## Output

After crawling, your `output/` directory will look like this:

```
output/
├── crawl_results_20260324_143022.csv        # CSV with all data
├── combined_content_20260324_143022.md      # All pages in one file
├── failed_urls_20260324_143022.txt          # (only if something failed)
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
Structured data file with columns: URL, Title, Success, Error, Markdown (+ Internal Links if using `--links`). Useful for data analysis or programmatic processing.

### Structured Folders
Recreates the website's URL hierarchy locally. Each page is saved as its own `.md` file named after the page title. Great for browsing content offline.

### Failed URLs
Any URL still failing after all retries is written to `failed_urls_<timestamp>.txt`, with the reason as a comment above each one. The file is itself a valid URL list, so a follow-up run is just:

```bash
python crawler.py output/failed_urls_20260324_143022.txt
```

## Proxy Support

Crawling a large URL list from one IP gets rate-limited or blocked. The crawler can route every request through [ProxyScrape](https://proxyscrape.com/) residential proxies instead. This is optional — with no `.env` present, crawls go out directly.

### Setup

1. Copy `.env.example` to `.env` and fill in the username and password from your ProxyScrape dashboard (**Residential → Proxy Setup**):

   ```bash
   PROXY_ENABLED=true
   PROXYSCRAPE_USERNAME=your-username
   PROXYSCRAPE_PASSWORD=your-password
   ```

   `.env` is gitignored — keep your credentials out of version control.

2. Confirm it works before running a real crawl:

   ```bash
   python crawler.py --check-proxy
   ```

   This makes one request per session and prints the exit IP it came from.

3. Crawl as normal. With `PROXY_ENABLED=true`, every run goes through the proxy.

### Settings

All of these live in `.env`, and the CLI flags override them per run.

| Variable | Default | Description |
|----------|---------|-------------|
| `PROXY_ENABLED` | `false` | Whether to use the proxy by default |
| `PROXYSCRAPE_USERNAME` | — | Your ProxyScrape username |
| `PROXYSCRAPE_PASSWORD` | — | Your ProxyScrape password |
| `PROXYSCRAPE_HOST` | `rp.scrapegw.com` | Proxy endpoint hostname |
| `PROXYSCRAPE_PORT` | `6060` | Proxy endpoint port |
| `PROXYSCRAPE_PROTOCOL` | `http` | Proxy protocol |
| `PROXYSCRAPE_COUNTRY` | *(any)* | Country code for exit nodes, e.g. `us`, `gb`, `de` |
| `PROXYSCRAPE_SESSIONS` | `1` | Number of sticky sessions to spread requests over |
| `PROXYSCRAPE_SESSION_LIFETIME` | `10` | Minutes a sticky session keeps its IP (1–120) |

If your dashboard shows a different endpoint (datacenter or mobile rather than residential), change `PROXYSCRAPE_HOST` and `PROXYSCRAPE_PORT` to match.

### Rotating vs sticky sessions

With `PROXYSCRAPE_SESSIONS=1` the crawler uses ProxyScrape's rotating endpoint, which picks a fresh IP for each request.

**For a straightforward bulk crawl, leave this at 1.** The rotating endpoint spreads requests across many IPs by itself, which is exactly what you want to avoid rate limits. Set it higher only if a site starts rejecting requests mid-crawl in a way that looks session-related — then each session keeps its own IP for `PROXYSCRAPE_SESSION_LIFETIME` minutes:

```bash
python crawler.py --proxy-sessions 5
```

Requests are spread across the sessions round-robin, and a retry deliberately moves to a different session so one bad IP cannot sink a URL.

### Per-run overrides

```bash
python crawler.py --proxy                  # Force the proxy on, whatever .env says
python crawler.py --no-proxy               # Crawl directly, whatever .env says
python crawler.py --proxy-country us       # Route via US exit nodes
python crawler.py --env-file prod.env      # Use a different credentials file
```

## Performance

Measured on a real 3,500-page site, 100 pages per run:

| | Throughput | Proxy bandwidth |
|---|---|---|
| Browser-based crawling | 5.4 pages/s | ~575 KB/page |
| This crawler | **19.1 pages/s** | **~34 KB/page** |

The difference is that a browser downloads stylesheets, fonts and images that the markdown extractor never reads, and costs roughly 2.5x a plain HTTP client in metered proxy traffic even with assets blocked.

### Concurrency

The default of 40 is the measured optimum. Fetching is cheap, but extraction is CPU-bound, so beyond ~40 the parsing work becomes the bottleneck:

| Concurrency | Throughput |
|---|---|
| 20 | 16.6 pages/s |
| **40** | **19.1 pages/s** |
| 80 | 15.7 pages/s |

Lower it if you are being rate-limited or want to be kinder to the target site:

```bash
python crawler.py --concurrency 10
```

### Retries

Each URL is retried on timeouts, connection errors and transient HTTP statuses (429, 500, 502, 503, 504), with exponential backoff and jitter. Permanent failures like 404 are not retried. With several proxy sessions configured, each retry moves to a different session.

```bash
python crawler.py --retries 3 --timeout 45
```

## Project Structure

| File | Purpose |
|------|---------|
| `crawler.py` | CLI, configuration and orchestration |
| `fetching.py` | Concurrent HTTP fetching, retries, proxy rotation, progress |
| `extraction.py` | HTML → markdown, title and link extraction |
| `outputs.py` | Writing CSV, combined markdown, folders and failure lists |
| `proxy.py` | ProxyScrape credentials, sticky sessions and health checks |

## License

[MIT](LICENSE)
