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


def _wordmark(path):
    """A stand-in logo: dark lowercase-style bars on a white square, with a blob above (like a tree)."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (600, 600), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([40, 320, 560, 400], fill=(44, 36, 62))      # x-height band: rows 320-400
    d.ellipse([240, 170, 330, 320], fill=(44, 36, 62))       # tree above the letters
    d.rectangle([40, 400, 60, 430], fill=(44, 36, 62))       # descender below
    im.save(path)


def _ring_icon(path):
    """Thin dark circle on a big white square, like a line-art icon with lots of padding."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (664, 664), "white")
    ImageDraw.Draw(im).ellipse([132, 132, 532, 532], outline=(44, 36, 62), width=7)
    im.save(path, quality=90)


def test_icon_processing(tmp_path):
    from app import branding
    from PIL import Image
    import io
    _ring_icon(tmp_path / "icon.jpg")
    small = Image.open(io.BytesIO(branding.build_icon(tmp_path / "icon.jpg", 32))).convert("L")
    assert small.size == (32, 32)
    # trimmed: the ring now reaches close to the edges (6% margin) instead of 20%
    row = [small.getpixel((x, 16)) for x in range(32)]
    first_dark = next(i for i, v in enumerate(row) if v < 160)
    assert first_dark <= 3
    # thickened: the ring is still clearly dark at 32 px, not a faint grey
    assert min(row) < 110
    big = Image.open(io.BytesIO(branding.build_icon(tmp_path / "icon.jpg", 512)))
    assert big.size == (512, 512)


def test_logo_processing(tmp_path):
    from app import branding
    from PIL import Image
    import io
    _wordmark(tmp_path / "logo.png")
    logo = branding.build(tmp_path / "logo.png")
    assert (logo.width, logo.height) == (521, 261)            # trimmed to the ink (40-560, 170-430)
    assert abs(logo.xheight_top - 150) <= 2 and abs(logo.baseline - 230) <= 2
    css = logo.css
    assert abs(css["height"] - 261 / 80) < 0.1                # whole logo is ~3.3 x-heights tall
    assert abs(css["drop"] - 31 / 80) < 0.1                   # descender hangs below the baseline
    im = Image.open(io.BytesIO(logo.png))
    assert im.mode == "RGBA"
    assert im.getpixel((5, 5))[3] == 0                        # white background is now transparent
    assert im.getpixel((100, 190))[3] == 255                  # letters are solid
    bigger = branding.build(tmp_path / "logo.png", scale=1.25)
    assert bigger.css["height"] > css["height"]


def test_branding_files(env, fixtures):
    c = TestClient(create_app(fetcher_factory=lambda: FakeFetcher({})))
    assert c.get("/brand/logo").status_code == 404
    page = c.get("/").text
    assert "Our parkruns</h1>" in page
    assert c.get("/favicon.ico").headers["content-type"].startswith("image/svg")   # built-in fallback
    _wordmark(env / "logo.png")
    _ring_icon(env / "icon.jpg")
    page = c.get("/").text
    assert 'class="brand-logo" role="img" aria-label="parkruns"' in page and "--logo-h:" in page
    assert "/brand/icon-32.png?v=" in page and "?v=0" not in page
    mask = c.get("/brand/logo-mask.png")
    assert mask.status_code == 200 and mask.headers["content-type"] == "image/png"
    from PIL import Image
    import io
    for path, size in [("/favicon.ico", 32), ("/brand/icon-32.png", 32), ("/apple-touch-icon.png", 180),
                       ("/brand/icon-512.png", 512)]:
        r = c.get(path)
        assert r.headers["content-type"] == "image/png", path
        assert Image.open(io.BytesIO(r.content)).size == (size, size)
    assert c.get("/brand/icon-99.png").status_code == 404
    m = c.get("/manifest.webmanifest").json()
    assert [i["sizes"] for i in m["icons"]] == ["192x192", "512x512"]


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
