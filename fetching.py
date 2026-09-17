#!/usr/bin/env python3
"""
Concurrent HTTP fetching, with optional proxying and retries.

Fetching is plain HTTP rather than a browser: these pages serve their content
as static HTML, so a browser would download stylesheets, fonts and images that
the markdown extractor never reads.
"""

import asyncio
import random
import time
from dataclasses import dataclass, field

import httpx

from proxy import ProxyEndpoint

# A real browser's UA. Some sites serve degraded HTML to obvious bots.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Status codes worth trying again: transient server and rate-limit responses.
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 522, 524}

DEFAULT_CONCURRENCY = 40


@dataclass
class FetchResult:
    """Outcome of fetching one URL."""

    url: str                    # the URL as it was requested
    ok: bool
    html: str = ""
    status: int | None = None
    final_url: str = ""         # after redirects
    error: str = ""
    attempts: int = 1


@dataclass
class FetchProgress:
    """Prints a running counter with throughput and an ETA."""

    total: int
    done: int = 0
    failed: int = 0
    started: float = field(default_factory=time.perf_counter)
    quiet: bool = False

    def _suffix(self) -> str:
        """Rate and ETA, once there is enough data for them to mean anything."""
        elapsed = time.perf_counter() - self.started
        if self.done < 5 or elapsed <= 0:
            return ""
        rate = self.done / elapsed
        remaining = int((self.total - self.done) / rate) if rate > 0 else 0
        return f" | {rate:.1f}/s, ETA {remaining // 60}m{remaining % 60:02d}s"

    def record(self, ok: bool, url: str, detail: str) -> None:
        self.done += 1
        if not ok:
            self.failed += 1
        if self.quiet:
            return
        icon = "✅" if ok else "❌"
        print(f"[{self.done}/{self.total}] {icon} {url} {detail}{self._suffix()}")

    def summary(self) -> str:
        elapsed = time.perf_counter() - self.started
        succeeded = self.done - self.failed
        if elapsed <= 0:
            return f"{succeeded}/{self.done} succeeded"
        return (
            f"{succeeded}/{self.done} succeeded in "
            f"{elapsed // 60:.0f}m{elapsed % 60:04.1f}s ({self.done / elapsed:.1f} pages/s)"
        )


def _describe_error(exc: Exception) -> str:
    """One-line, readable description of a fetch failure."""
    if isinstance(exc, httpx.TimeoutException):
        return "Timeout"
    if isinstance(exc, httpx.ProxyError):
        return f"Proxy error: {exc}"
    if isinstance(exc, httpx.ConnectError):
        return f"Connection failed: {exc}"
    if isinstance(exc, httpx.TooManyRedirects):
        return "Too many redirects"
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return f"{type(exc).__name__}: {message}"[:200]


class Fetcher:
    """
    Fetches URLs concurrently, over one HTTP client per proxy endpoint.

    With several proxy sessions configured, requests are spread across them
    round-robin, so consecutive requests leave from different IPs. A retry
    deliberately moves to the next client, so a bad session cannot sink a URL.
    """

    def __init__(
        self,
        endpoints: list[ProxyEndpoint] | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        timeout: float = 30.0,
        retries: int = 2,
    ):
        self.endpoints = endpoints or []
        self.concurrency = max(1, concurrency)
        self.timeout = timeout
        self.retries = max(0, retries)
        self._clients: list[httpx.AsyncClient] = []
        self._next = 0

    async def __aenter__(self) -> "Fetcher":
        # One client per proxy endpoint, or a single direct client.
        proxies = [e.url for e in self.endpoints] or [None]
        per_client = max(4, self.concurrency // len(proxies) + 1)
        self._clients = [
            httpx.AsyncClient(
                proxy=proxy,
                timeout=httpx.Timeout(self.timeout, connect=min(15.0, self.timeout)),
                follow_redirects=True,
                headers=DEFAULT_HEADERS,
                limits=httpx.Limits(
                    max_connections=per_client,
                    max_keepalive_connections=per_client,
                ),
            )
            for proxy in proxies
        ]
        return self

    async def __aexit__(self, *exc_info) -> None:
        for client in self._clients:
            await client.aclose()
        self._clients = []

    def _client_for(self, attempt: int) -> httpx.AsyncClient:
        """Round-robin, offset by attempt so a retry lands on a different session."""
        self._next += 1
        return self._clients[(self._next + attempt) % len(self._clients)]

    async def _fetch_one(self, url: str) -> FetchResult:
        """Fetch a single URL, retrying transient failures with backoff."""
        last_error = "Unknown error"
        last_status: int | None = None

        for attempt in range(self.retries + 1):
            if attempt:
                # Back off with jitter so retries do not all land together.
                await asyncio.sleep(min(8.0, 2 ** attempt) * (0.5 + random.random()))

            client = self._client_for(attempt)
            try:
                response = await client.get(url)
            except Exception as exc:
                last_error = _describe_error(exc)
                continue

            last_status = response.status_code
            if response.is_success:
                return FetchResult(
                    url=url,
                    ok=True,
                    html=response.text,
                    status=response.status_code,
                    final_url=str(response.url),
                    attempts=attempt + 1,
                )

            last_error = f"HTTP {response.status_code}"
            if response.status_code not in RETRYABLE_STATUS:
                break  # 404s and the like will not improve on a retry

        return FetchResult(
            url=url, ok=False, status=last_status, error=last_error,
            attempts=self.retries + 1,
        )

    async def fetch_all(self, urls: list[str], on_result=None) -> list[FetchResult]:
        """
        Fetch every URL, calling on_result(FetchResult) as each one completes.

        on_result runs while other requests are still in flight, so the caller's
        processing overlaps with fetching rather than waiting for the whole batch.
        It may be a plain function or a coroutine function.
        """
        semaphore = asyncio.Semaphore(self.concurrency)
        results: list[FetchResult] = []

        async def worker(url: str) -> None:
            async with semaphore:
                result = await self._fetch_one(url)
            if on_result is not None:
                try:
                    outcome = on_result(result)
                    if asyncio.iscoroutine(outcome):
                        await outcome
                except Exception as exc:
                    # A handler that raises must not abort every other request.
                    result.ok = False
                    result.error = f"Processing failed: {type(exc).__name__}: {exc}"[:200]
            # Drop the body once it has been handled: holding every page's HTML
            # would cost gigabytes on a large crawl, and the caller has had it.
            result.html = ""
            results.append(result)

        await asyncio.gather(*(worker(u) for u in urls))
        return results
