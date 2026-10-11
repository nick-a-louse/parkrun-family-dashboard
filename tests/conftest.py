import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = Path(__file__).parent / "fixtures"


class FakeFetcher:
    """Serves fixture pages instead of hitting parkrun."""

    name = "fake"

    def __init__(self, pages: dict[str, str]):
        self.pages = pages
        self.requests = 0
        self.paths = []

    def get(self, path):
        self.requests += 1
        self.paths.append(path)
        if path not in self.pages:
            from app.fetcher import FetchError
            raise FetchError(f"HTTP 404 for {path}")
        page = self.pages[path]
        if isinstance(page, Exception):
            raise page
        return page

    def profile(self, aid):
        return self.get(f"/parkrunner/{aid}/all/")

    def event(self, slug, n):
        return self.get(f"/{slug}/results/{n}/")

    def close(self):
        pass


class FakeNotifier:
    enabled = True

    def __init__(self):
        self.sent = []
        self.alerts = []

    def send_new_results(self, keys):
        self.sent.append(keys)

    def send_failure_alert(self, n, message):
        self.alerts.append((n, message))


@pytest.fixture
def fixtures():
    return {p.name: p.read_text() for p in FIX.glob("*.html")}


@pytest.fixture
def env(tmp_path, monkeypatch):
    runners = tmp_path / "runners.yaml"
    runners.write_text(
        "runners:\n"
        "  - name: Sam\n    id: A1000003\n    expect: [saturday]\n"
        "  - name: Ava\n    id: A1000001\n"
        "  - name: Max\n    id: 1000002\n"
    )
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("RUNNERS_FILE", str(runners))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("REQUEST_DELAY", "0")
    monkeypatch.setenv("BASE_URL", "http://gomez.test:5060")
    return tmp_path
