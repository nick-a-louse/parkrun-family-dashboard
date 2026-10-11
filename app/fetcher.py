"""Polite page fetching from parkrun.

The fetcher is swappable via the FETCHER setting:

* ``http``    (default) plain HTTPS requests with normal browser headers
* ``browser`` a headless Chromium via Playwright (image built with INSTALL_BROWSER=true)
* ``manual``  no network at all: pages are imported by uploading saved HTML

Every fetcher waits a random few seconds between requests so a sync looks like
a person clicking through three or four pages.
"""
from __future__ import annotations

import logging
import random
import time

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Cache-Control": "no-cache",
}


class FetchError(Exception):
    pass


class FetchBlocked(FetchError):
    """parkrun refused the request (usually HTTP 403/405 from its firewall)."""


class Fetcher:
    name = "base"

    def __init__(self, domain: str, delay: tuple[float, float] = (3, 6)):
        self.domain = domain
        self.delay = delay
        self.requests = 0
        self._last = 0.0

    def url(self, path: str) -> str:
        return f"https://{self.domain}{path}"

    def _pause(self):
        if self._last:
            wait = random.uniform(*self.delay) - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
        self._last = time.monotonic()

    def get(self, path: str) -> str:
        self._pause()
        self.requests += 1
        return self._get(self.url(path))

    def _get(self, url: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def close(self):
        pass

    # convenience
    def profile(self, athlete_id: int) -> str:
        return self.get(f"/parkrunner/{athlete_id}/all/")

    def event(self, slug: str, run_number: int) -> str:
        return self.get(f"/{slug}/results/{run_number}/")


class HttpFetcher(Fetcher):
    name = "http"

    def __init__(self, domain, delay=(3, 6), client=None):
        super().__init__(domain, delay)
        import httpx
        self._client = client or httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True)

    def _get(self, url: str) -> str:
        import httpx
        last_exc = None
        for attempt in range(3):
            try:
                r = self._client.get(url)
            except httpx.HTTPError as e:
                last_exc = e
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code in (403, 405):
                raise FetchBlocked(f"parkrun refused the request (HTTP {r.status_code}) for {url}")
            if r.status_code in (429, 500, 502, 503, 504):
                last_exc = FetchError(f"HTTP {r.status_code} for {url}")
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise FetchError(f"HTTP {r.status_code} for {url}")
            return r.text
        raise FetchError(f"Gave up after 3 attempts: {last_exc}")

    def close(self):
        self._client.close()


class BrowserFetcher(Fetcher):
    name = "browser"

    def __init__(self, domain, delay=(3, 6)):
        super().__init__(domain, delay)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover - depends on image
            raise FetchError("FETCHER=browser needs the image built with INSTALL_BROWSER=true") from e
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._page = self._browser.new_page(user_agent=USER_AGENT, locale="en-GB")

    def _get(self, url: str) -> str:
        resp = self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if resp is not None and resp.status in (403, 405):
            raise FetchBlocked(f"parkrun refused the request (HTTP {resp.status}) for {url}")
        if resp is not None and resp.status != 200:
            raise FetchError(f"HTTP {resp.status} for {url}")
        return self._page.content()

    def close(self):
        try:
            self._browser.close()
        finally:
            self._pw.stop()


class ManualFetcher(Fetcher):
    name = "manual"

    def _get(self, url: str) -> str:
        raise FetchError("Automatic fetching is turned off (FETCHER=manual). Import saved pages instead.")


def make_fetcher(kind: str, domain: str, delay=(3, 6)) -> Fetcher:
    kind = (kind or "http").lower()
    if kind == "http":
        return HttpFetcher(domain, delay)
    if kind == "browser":
        return BrowserFetcher(domain, delay)
    if kind == "manual":
        return ManualFetcher(domain, delay)
    raise ValueError(f"Unknown FETCHER {kind!r}; use http, browser or manual")
