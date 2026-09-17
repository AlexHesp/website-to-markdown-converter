#!/usr/bin/env python3
"""
Proxy configuration, targeting ProxyScrape residential proxies.

Credentials live in a .env file (never commit it) and are read into a
ProxySettings object, which expands into one or more ProxyEndpoints.

ProxyScrape residential endpoint: rp.scrapegw.com:6060
Username modifiers (appended to the base username):
  -country-{code}                      e.g. -country-us
  -session-{id}-lifetime-{minutes}     sticky IP for up to 120 minutes
"""

import asyncio
import os
import random
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional; plain env vars still work
    load_dotenv = None


# ProxyScrape residential defaults
DEFAULT_HOST = "rp.scrapegw.com"
DEFAULT_PORT = 6060
DEFAULT_PROTOCOL = "http"
DEFAULT_SESSION_LIFETIME = 10  # minutes; ProxyScrape recommends <= 120

# Endpoint used to confirm traffic is leaving via the proxy
IP_CHECK_URL = "https://ipv4.icanhazip.com"


class ProxyConfigError(RuntimeError):
    """Raised when proxy settings are requested but incomplete or invalid."""


@dataclass(frozen=True)
class ProxyEndpoint:
    """One proxy the fetcher can send requests through."""

    url: str      # http://user:pass@host:port, ready for httpx
    label: str    # human-readable, with no password in it

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class ProxySettings:
    """Resolved proxy settings, before being expanded into sticky sessions."""

    username: str
    password: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    protocol: str = DEFAULT_PROTOCOL
    country: str | None = None
    sessions: int = 1
    session_lifetime: int = DEFAULT_SESSION_LIFETIME

    @property
    def server(self) -> str:
        return f"{self.protocol}://{self.host}:{self.port}"

    def describe(self) -> str:
        """Human-readable summary with the password withheld."""
        bits = [self.server, f"user={_mask(self.username)}"]
        if self.country:
            bits.append(f"country={self.country}")
        if self.sessions > 1:
            bits.append(f"sessions={self.sessions} (lifetime {self.session_lifetime}m)")
        return ", ".join(bits)


def _mask(value: str) -> str:
    """Mask a credential, keeping just enough to recognise it."""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:3]}{'*' * (len(value) - 3)}"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ProxyConfigError(f"{name} must be an integer, got {raw!r}") from exc


def load_env(env_file: str | Path | None = None) -> None:
    """
    Load a .env file into the process environment.

    Only the given path is read (default: .env in the working directory) so the
    source of credentials is never ambiguous. Variables already set in the
    environment win, and a missing file is not an error - plain environment
    variables work on their own.
    """
    if load_dotenv is None:
        return
    path = Path(env_file) if env_file else Path(".env")
    if path.exists():
        load_dotenv(path, override=False)


def load_proxy_settings(
    env_file: str | Path | None = None,
    enabled: bool | None = None,
    sessions: int | None = None,
    country: str | None = None,
) -> ProxySettings | None:
    """
    Build ProxySettings from the environment.

    Args:
        env_file: Path to the .env file (default: .env in the working directory)
        enabled: Force proxy on/off, overriding PROXY_ENABLED
        sessions: Override PROXYSCRAPE_SESSIONS
        country: Override PROXYSCRAPE_COUNTRY

    Returns:
        ProxySettings, or None when the proxy is disabled.

    Raises:
        ProxyConfigError: proxy is enabled but credentials are missing/invalid.
    """
    load_env(env_file)

    use_proxy = _env_flag("PROXY_ENABLED", default=False) if enabled is None else enabled
    if not use_proxy:
        return None

    username = (os.getenv("PROXYSCRAPE_USERNAME") or "").strip()
    password = (os.getenv("PROXYSCRAPE_PASSWORD") or "").strip()
    if not username or not password:
        raise ProxyConfigError(
            "Proxy is enabled but PROXYSCRAPE_USERNAME / PROXYSCRAPE_PASSWORD are not set.\n"
            "Copy .env.example to .env and fill in your ProxyScrape credentials,\n"
            "or crawl without a proxy using --no-proxy."
        )

    session_count = _env_int("PROXYSCRAPE_SESSIONS", 1) if sessions is None else sessions
    if session_count < 1:
        raise ProxyConfigError(f"Session count must be >= 1, got {session_count}")

    lifetime = _env_int("PROXYSCRAPE_SESSION_LIFETIME", DEFAULT_SESSION_LIFETIME)
    if not 1 <= lifetime <= 120:
        raise ProxyConfigError(f"Session lifetime must be 1-120 minutes, got {lifetime}")

    country_code = country if country is not None else (os.getenv("PROXYSCRAPE_COUNTRY") or "").strip()

    return ProxySettings(
        username=username,
        password=password,
        host=(os.getenv("PROXYSCRAPE_HOST") or DEFAULT_HOST).strip(),
        port=_env_int("PROXYSCRAPE_PORT", DEFAULT_PORT),
        protocol=(os.getenv("PROXYSCRAPE_PROTOCOL") or DEFAULT_PROTOCOL).strip().lower(),
        country=country_code.lower() or None,
        sessions=session_count,
        session_lifetime=lifetime,
    )


def build_username(settings: ProxySettings, session_id: str | None = None) -> str:
    """Apply ProxyScrape username modifiers for country targeting and sticky sessions."""
    username = settings.username
    if settings.country:
        username += f"-country-{settings.country}"
    if session_id:
        username += f"-session-{session_id}-lifetime-{settings.session_lifetime}"
    return username


def build_proxy_endpoints(settings: ProxySettings) -> list[ProxyEndpoint]:
    """
    Expand settings into endpoints the fetcher can use.

    One endpoint means the rotating endpoint, where ProxyScrape picks a fresh IP
    per request. More than one means N sticky sessions to spread requests over,
    each holding its own IP for session_lifetime minutes.
    """
    usernames = (
        [build_username(settings)]
        if settings.sessions <= 1
        else [
            build_username(settings, str(random.randint(10_000_000, 99_999_999)))
            for _ in range(settings.sessions)
        ]
    )

    endpoints = []
    for i, username in enumerate(usernames, 1):
        creds = f"{quote(username, safe='')}:{quote(settings.password, safe='')}"
        host_port = settings.server.split("://", 1)[1]
        label = f"session {i}" if settings.sessions > 1 else "rotating endpoint"
        endpoints.append(
            ProxyEndpoint(url=f"{settings.protocol}://{creds}@{host_port}", label=label)
        )
    return endpoints


@dataclass
class ProxyCheck:
    """Result of a single proxy health check."""

    endpoint: ProxyEndpoint
    ok: bool
    exit_ip: str = ""
    error: str = ""


async def check_proxy(endpoint: ProxyEndpoint, timeout: float = 20.0) -> ProxyCheck:
    """Make one request through the proxy and report the exit IP."""
    try:
        async with httpx.AsyncClient(proxy=endpoint.url, timeout=timeout) as client:
            response = await client.get(IP_CHECK_URL)
            response.raise_for_status()
            return ProxyCheck(endpoint=endpoint, ok=True, exit_ip=response.text.strip())
    except Exception as exc:
        return ProxyCheck(endpoint=endpoint, ok=False, error=f"{type(exc).__name__}: {exc}")


async def check_proxies(endpoints: list[ProxyEndpoint], timeout: float = 20.0) -> list[ProxyCheck]:
    """Health-check every endpoint concurrently."""
    return await asyncio.gather(*(check_proxy(e, timeout) for e in endpoints))
