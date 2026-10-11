from fastapi.testclient import TestClient

from app import grafana_setup
from app.main import create_app
from conftest import FakeFetcher
from test_sync import base_pages


def test_metrics_endpoint(env, fixtures):
    app = create_app(fetcher_factory=lambda: FakeFetcher(base_pages(fixtures)))
    c = TestClient(app)
    body = c.get("/metrics").text
    assert "parkrun_consecutive_failures 0" in body
    assert "parkrun_last_success_timestamp_seconds" not in body.split("# TYPE parkrun_last_success_timestamp_seconds gauge")[1].split("#")[0]  # no value yet
    app.state.syncer.sync("manual")
    r = c.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert 'parkrun_results{runner="Ava",distance="2k"} 3' in body
    assert 'parkrun_results{runner="Max",distance="5k"} 1' in body
    assert 'parkrun_checks_total{result="failed"} 1' in body      # a fixture event page 404s
    assert "parkrun_last_check_ok 0" in body
    assert "parkrun_consecutive_failures 1" in body
    assert 'parkrun_info{version="' in body


def test_branding_files(env, fixtures):
    c = TestClient(create_app(fetcher_factory=lambda: FakeFetcher({})))
    assert c.get("/brand/logo").status_code == 404
    page = c.get("/").text
    assert "Our parkruns</h1>" in page
    assert c.get("/favicon.ico").headers["content-type"].startswith("image/svg")   # built-in fallback
    (env / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (env / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\nicon")
    page = c.get("/").text
    assert '<img class="brand-logo" src="/brand/logo"' in page
    assert c.get("/brand/logo").content.endswith(b"fake")
    assert c.get("/apple-touch-icon.png").content.endswith(b"icon")
    m = c.get("/manifest.webmanifest").json()
    assert m["icons"][0]["type"] == "image/png"


def test_grafana_setup(monkeypatch, capsys):
    calls = []

    def fake_call(self, method, path, body=None):
        calls.append((method, path, body))
        if path == "/api/health" or path == "/api/user":
            return 200, {}
        if path == "/api/datasources":
            return 200, [{"name": "Loki", "type": "loki", "uid": "l1"},
                         {"name": "VictoriaMetrics", "type": "prometheus", "uid": "vm1", "url": "http://victoriametrics:8428"}]
        if path == "/api/v1/provisioning/alert-rules" and method == "GET":
            return 200, [{"uid": "x", "ruleGroup": "Server Alerts", "folderUID": "fold1"}]
        if path == "/api/v1/provisioning/alert-rules/parkrun-down" and method == "GET":
            return 200, {}            # exists already -> PUT
        if method == "GET" and path.startswith("/api/v1/provisioning/alert-rules/"):
            return 404, {}
        if path == "/api/dashboards/db":
            return 200, {"url": "/d/parkrun/parkrun-dashboard"}
        return 201, {}

    monkeypatch.setattr(grafana_setup.Grafana, "call", fake_call)
    monkeypatch.setenv("GRAFANA_PASSWORD", "secret")
    assert grafana_setup.main([]) == 0
    out = capsys.readouterr().out
    assert "Updated alert: Parkrun Dashboard Down" in out
    assert "Created alert: Parkrun Checks Failing" in out
    assert "Created alert: Parkrun Results Not Updating" in out
    puts = [b for m, p, b in calls if m in ("PUT", "POST") and "alert-rules" in p]
    assert all(r["folderUID"] == "fold1" and r["data"][0]["datasourceUid"] == "vm1" for r in puts)
    dash = [b for m, p, b in calls if p == "/api/dashboards/db"][0]
    assert dash["folderUid"] == "fold1" and dash["dashboard"]["uid"] == "parkrun"


def test_grafana_setup_needs_password(monkeypatch):
    monkeypatch.delenv("GRAFANA_PASSWORD", raising=False)
    assert grafana_setup.main([]) == 2
