"""The adapter registry, and the retry and error handling every source shares.

Failures are values rather than exceptions: one dead board must never take the
morning brief down with it. A source that cannot run because its API key is
absent is a third state — neither success nor failure — so that an unconfigured
aggregator reads as "off" in `check-sources` instead of as a fault.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Sequence

import requests

from ..config import FetchSettings, Profile, Source
from ..model import RawPosting

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """The source was reachable but unusable — bad shape, bad status, no data."""


class SourceSkipped(RuntimeError):
    """The source cannot run yet, and that is not a fault (usually: no API key)."""


#: kind -> adapter. Populated by @register at import time.
Adapter = Callable[[Source, Profile, FetchSettings, requests.Session], list[RawPosting]]
ADAPTERS: dict[str, Adapter] = {}


def register(kind: str) -> Callable[[Adapter], Adapter]:
    def decorate(function: Adapter) -> Adapter:
        ADAPTERS[kind] = function
        return function

    return decorate


def short_error(exc: Exception) -> str:
    """A network failure in words that fit in an e-mail footer.

    `requests` renders a refused connection as three nested exceptions and a
    full URL — around 300 characters, which is unreadable in the digest footer
    and in the check-sources table alike. What a reader needs is which of the
    handful of things went wrong.
    """
    import requests as _requests

    if isinstance(exc, _requests.exceptions.ProxyError):
        return "blocked by a proxy"
    if isinstance(exc, _requests.exceptions.SSLError):
        return "TLS handshake failed"
    if isinstance(exc, _requests.exceptions.ConnectTimeout):
        return "timed out connecting"
    if isinstance(exc, _requests.exceptions.ReadTimeout):
        return "timed out waiting for a reply"
    if isinstance(exc, _requests.exceptions.TooManyRedirects):
        return "redirect loop"
    if isinstance(exc, _requests.exceptions.ConnectionError):
        return "could not connect — check the host is right"
    return type(exc).__name__


@dataclass
class FetchResult:
    """The outcome of one source."""

    source: Source
    postings: list[RawPosting] = field(default_factory=list)
    status_code: int | None = None
    error: str | None = None
    skipped: str | None = None
    elapsed_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None and self.skipped is None


def request_json(
    session: requests.Session,
    url: str,
    settings: FetchSettings,
    *,
    method: str = "GET",
    params: dict | None = None,
    json_body: dict | None = None,
) -> object:
    """One HTTP call returning decoded JSON, with the errors named usefully."""
    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "application/json",
    }
    if json_body is not None:
        headers["Content-Type"] = "application/json"

    response = session.request(
        method,
        url,
        headers=headers,
        params=params,
        json=json_body,
        timeout=settings.timeout_seconds,
        allow_redirects=True,
    )
    if response.status_code >= 400:
        raise SourceError(f"HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        # Nearly always a login wall or an error page served as HTML.
        raise SourceError(f"expected JSON, got {response.headers.get('Content-Type', '?')}") from exc


def fetch_source(
    source: Source,
    profile: Profile,
    settings: FetchSettings,
    session: requests.Session | None = None,
) -> FetchResult:
    """Run one source's adapter, retrying transient failures."""
    adapter = ADAPTERS.get(source.kind)
    if adapter is None:
        return FetchResult(source=source, error=f"no adapter for kind '{source.kind}'")

    owns_session = session is None
    session = session or requests.Session()
    started = time.monotonic()
    last_error = "unknown error"

    try:
        for attempt in range(settings.retries + 1):
            try:
                postings = adapter(source, profile, settings, session)
            except SourceSkipped as exc:
                return FetchResult(
                    source=source,
                    skipped=str(exc),
                    elapsed_seconds=time.monotonic() - started,
                )
            except SourceError as exc:
                last_error = str(exc)
                # A 4xx will not fix itself; a 429 or 5xx might.
                if not any(code in last_error for code in ("429", "500", "502", "503", "504")):
                    break
            except requests.RequestException as exc:
                last_error = short_error(exc)
            except (KeyError, TypeError, ValueError) as exc:
                # The board answered in a shape the adapter does not know.
                last_error = f"unexpected response shape ({type(exc).__name__}: {exc})"
                break
            else:
                return FetchResult(
                    source=source,
                    postings=postings,
                    elapsed_seconds=time.monotonic() - started,
                )

            if attempt < settings.retries:
                time.sleep(settings.backoff_seconds * (attempt + 1))
    finally:
        if owns_session:
            session.close()

    log.warning("source %s failed: %s", source.id, last_error)
    return FetchResult(
        source=source, error=last_error, elapsed_seconds=time.monotonic() - started
    )


def fetch_all(
    sources: Sequence[Source],
    profile: Profile,
    settings: FetchSettings,
) -> list[FetchResult]:
    """Fetch every source concurrently, preserving the configured order."""
    if not sources:
        return []

    from concurrent.futures import ThreadPoolExecutor
    import threading

    workers = min(settings.max_workers, len(sources))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # A session per worker thread; requests.Session is not thread-safe.
        local_sessions: dict[int, requests.Session] = {}
        lock = threading.Lock()

        def run(source: Source) -> FetchResult:
            key = threading.get_ident()
            with lock:
                session = local_sessions.get(key)
                if session is None:
                    session = requests.Session()
                    local_sessions[key] = session
            return fetch_source(source, profile, settings, session=session)

        try:
            return list(pool.map(run, sources))
        finally:
            for session in local_sessions.values():
                session.close()


def queries_for(source: Source, profile: Profile) -> list[str]:
    """The search phrases a source should run.

    A source may pin its own `query`; otherwise it runs every phrase in
    profile.yaml, so the search terms live in one place.
    """
    if source.query:
        return [source.query]
    return list(profile.queries) or [""]
