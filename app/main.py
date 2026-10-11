"""Web app: the dashboard page, JSON API, exports and manual refresh/import."""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .config import load_runners, load_settings
from .db import Database
from .exports import filtered, to_csv, to_json, to_xlsx
from .metrics import render as render_metrics
from .notify import Notifier
from .parser import format_time
from .scheduler import describe, start_scheduler
from .stats import DISTANCE_LABEL, format_pace, chart_series, load_results, summarise, weekend_of
from .sync import Syncer

log = logging.getLogger("parkrun")
HERE = Path(__file__).parent

# Validated categorical palette (light, dark), assigned in fixed order by runner.
PALETTE_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
PALETTE_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]


def create_app(settings=None, fetcher_factory=None) -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    settings = settings or load_settings()
    runners = load_runners(settings.runners_file)
    db = Database(settings.db_path)
    notifier = Notifier(settings, db, runners, HERE / "templates")
    syncer = Syncer(settings, db, runners, fetcher_factory=fetcher_factory, notifier=notifier)
    tz = ZoneInfo(settings.timezone)

    app = FastAPI(title="parkrun family dashboard", version=__version__, docs_url="/api/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters["fmt_time"] = format_time
    templates.env.filters["nice_date"] = lambda d: d.strftime("%a %-d %b %Y") if d else ""
    templates.env.filters["short_date"] = lambda d: d.strftime("%-d %b") if d else ""
    templates.env.filters["ordinal"] = _ordinal
    templates.env.filters["pace"] = format_pace
    templates.env.filters["local_time"] = lambda iso: _friendly(iso, tz)

    app.state.settings = settings
    app.state.db = db
    app.state.syncer = syncer
    app.state.notifier = notifier
    app.state.scheduler = None
    app.state.last_manual = 0.0

    def background_sync(trigger: str):
        threading.Thread(target=syncer.sync, args=(trigger,), daemon=True, name=f"sync-{trigger}").start()

    @app.on_event("startup")
    def _startup():
        if settings.scheduler_enabled:
            app.state.scheduler = start_scheduler(settings, syncer)
            # First start (or after a long outage): load history straight away.
            if db.get_state("last_success") is None:
                background_sync("startup")

    @app.on_event("shutdown")
    def _shutdown():
        if app.state.scheduler:
            app.state.scheduler.shutdown(wait=False)

    def build_view(today: date | None = None) -> dict:
        today = today or datetime.now(tz).date()
        results = load_results(db, runners)
        people = summarise(db, runners, results, PALETTE_LIGHT, today)
        dark_colours = {p.athlete_id: (runners[i].colour or PALETTE_DARK[i % len(PALETTE_DARK)])
                        for i, p in enumerate(people)}
        feed = sorted(results, key=lambda r: (r.run_date, r.runner), reverse=True)
        latest_weekend = weekend_of(feed[0].run_date) if feed else None
        series = chart_series(results, people)
        dark_by_name = {p.name: dark_colours[p.athlete_id] for p in people}
        for lines in series.values():
            for line in lines:
                line["colour_dark"] = dark_by_name[line["name"]]
        return {
            "results": results, "people": people, "feed": feed, "latest_weekend": latest_weekend,
            "series": series, "dark_colours": dark_colours,
            "cutoff": latest_weekend - timedelta(days=21) if latest_weekend else None,
            "status": status_payload(), "today": today,
        }

    def status_payload() -> dict:
        last_ok = db.get_state("last_success")
        last = db.one("SELECT * FROM fetch_log ORDER BY id DESC LIMIT 1")
        cooldown = max(0, int(settings.refresh_cooldown - (time.monotonic() - app.state.last_manual))) \
            if app.state.last_manual else 0
        return {
            "running": syncer.running,
            "last_success": _local(last_ok, tz),
            "last_success_text": _friendly(last_ok, tz),
            "last_attempt": dict(last) if last else None,
            "cooldown_seconds": cooldown,
            "fetcher": settings.fetcher,
            "email": settings.email_enabled,
            "schedule": describe(app.state.scheduler),
            "consecutive_failures": int(db.get_state("consecutive_failures", "0")),
        }

    # ------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        view = build_view()
        log_rows = db.query("SELECT * FROM fetch_log ORDER BY id DESC LIMIT 12")
        return templates.TemplateResponse(request, "index.html", {
            **view, "fetch_log": log_rows, "settings": settings, "distance_label": DISTANCE_LABEL,
            "has_logo": brand_file("logo") is not None, "has_icon": brand_file("icon") is not None,
            "version": __version__, "tz": tz,
        })

    # ------------------------------------------------------------ branding (files you supply in /config)
    brand_dir = settings.runners_file.parent

    def brand_file(stem: str):
        for ext in ("svg", "png", "webp", "jpg", "jpeg", "ico"):
            p = brand_dir / f"{stem}.{ext}"
            if p.is_file():
                return p
        return None

    app.state.brand_file = brand_file

    @app.get("/brand/logo")
    def brand_logo():
        p = brand_file("logo")
        if not p:
            raise HTTPException(404, "No logo file in the config folder")
        return FileResponse(p, headers={"Cache-Control": "max-age=3600"})

    @app.get("/brand/icon")
    @app.get("/favicon.ico")
    @app.get("/apple-touch-icon.png")
    def brand_icon():
        p = brand_file("icon")
        if p:
            return FileResponse(p, headers={"Cache-Control": "max-age=3600"})
        return FileResponse(HERE / "static" / "icon.svg", media_type="image/svg+xml")

    @app.get("/manifest.webmanifest")
    def manifest():
        p = brand_file("icon")
        mime = {"svg": "image/svg+xml", "png": "image/png", "webp": "image/webp", "jpg": "image/jpeg",
                "jpeg": "image/jpeg", "ico": "image/x-icon"}[p.suffix[1:]] if p else "image/svg+xml"
        return JSONResponse({
            "name": "Our parkruns", "short_name": "parkruns", "start_url": "/", "display": "standalone",
            "background_color": "#eef1ec", "theme_color": "#17231d",
            "icons": [{"src": "/brand/icon", "sizes": "any", "type": mime, "purpose": "any"}],
        }, media_type="application/manifest+json")

    @app.get("/metrics")
    def metrics_endpoint():
        nxt = None
        if app.state.scheduler:
            times = [j.next_run_time for j in app.state.scheduler.get_jobs() if j.next_run_time]
            nxt = min(times) if times else None
        body = render_metrics(db, runners, syncer.running, nxt)
        return Response(body, media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/healthz")
    def healthz():
        db.one("SELECT 1")
        return {"ok": True, "version": __version__}

    # ------------------------------------------------------------ actions
    @app.post("/api/refresh")
    def refresh():
        if syncer.running:
            return JSONResponse({"started": False, "message": "Already checking for results"}, status_code=409)
        wait = settings.refresh_cooldown - (time.monotonic() - app.state.last_manual)
        if app.state.last_manual and wait > 0:
            return JSONResponse({"started": False, "cooldown_seconds": int(wait),
                                 "message": f"Refreshed recently. Try again in {_minutes(wait)}."},
                                status_code=429)
        app.state.last_manual = time.monotonic()
        before = db.one("SELECT COALESCE(MAX(id), 0) AS id FROM fetch_log")["id"]
        background_sync("manual")
        return JSONResponse({"started": True, "after_log_id": before}, status_code=202)

    @app.get("/api/status")
    def status():
        return status_payload()

    @app.post("/api/import")
    async def import_page(file: UploadFile = File(...)):
        raw = await file.read()
        if len(raw) > 5_000_000:
            raise HTTPException(413, "That file is too big to be a parkrun page")
        out = syncer.import_html(raw.decode("utf-8", errors="replace"))
        return JSONResponse({"ok": out.ok, "message": out.message, "new_results": out.new_results},
                            status_code=200 if out.ok else 422)

    @app.post("/api/test-email")
    def test_email():
        if not settings.email_enabled:
            raise HTTPException(400, "Email isn't set up. Set SMTP_HOST, EMAIL_FROM and EMAIL_TO.")
        try:
            notifier.send_test()
        except Exception as e:
            raise HTTPException(502, f"Couldn't send: {e}")
        return {"ok": True, "message": f"Test email sent to {', '.join(settings.email_to)}"}

    # ------------------------------------------------------------ JSON API (Home Assistant etc.)
    @app.get("/api/results")
    def api_results(runner: str | None = None, distance: str | None = Query(None, pattern="^(2k|5k)$")):
        return {"results": filtered(load_results(db, runners), runner, distance)}

    @app.get("/api/latest")
    def api_latest():
        results = load_results(db, runners)
        out = {}
        for r in runners:
            mine = [x for x in results if x.athlete_id == r.athlete_id]
            out[r.name] = max(mine, key=lambda x: x.run_date).as_dict() if mine else None
        return {"latest": out, "last_updated": status_payload()["last_success"]}

    @app.get("/api/summary")
    def api_summary():
        view = build_view()
        out = {}
        for p in view["people"]:
            out[p.name] = {
                "parkrun_id": f"A{p.athlete_id}",
                "last_run": p.last_run.isoformat() if p.last_run else None,
                "current_streak_weekends": p.current_streak,
                "longest_streak_weekends": p.longest_streak,
                "distances": {d.distance: {
                    "runs": d.runs,
                    "pb": d.pb.time if d.pb else None,
                    "pb_seconds": d.pb.time_seconds if d.pb else None,
                    "pb_date": d.pb.run_date.isoformat() if d.pb else None,
                    "pb_pace": d.pb.pace if d.pb else None,
                    "average_pace": format_pace(d.avg_pace) if d.avg_pace else None,
                    "best_this_year": d.year_best.time if d.year_best else None,
                    "latest_age_grade": d.latest_age_grade,
                    "best_age_grade": d.best_age_grade,
                    "best_position": d.best_position,
                    "best_gender_position": d.best_gender_position,
                    "milestones": d.milestones_reached,
                    "next_milestone": d.next_milestone[0] if d.next_milestone else None,
                    "runs_to_next_milestone": d.next_milestone[1] if d.next_milestone else None,
                } for d in p.distances},
            }
        return {"runners": out, "last_updated": status_payload()["last_success"]}

    # ------------------------------------------------------------ exports
    @app.get("/export/results.{fmt}")
    def export(fmt: str, runner: str | None = None, distance: str | None = Query(None, pattern="^(2k|5k)$")):
        rows = filtered(load_results(db, runners), runner, distance)
        stamp = date.today().isoformat()
        name = f"parkrun-results-{stamp}"
        if fmt == "csv":
            return Response(to_csv(rows), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
        if fmt == "json":
            return Response(to_json(rows), media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="{name}.json"'})
        if fmt == "xlsx":
            return Response(to_xlsx(rows),
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})
        raise HTTPException(404, "Use .csv, .json or .xlsx")

    return app


def _local(iso: str | None, tz: ZoneInfo) -> str | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso).astimezone(tz).isoformat()


def _friendly(iso: str | None, tz: ZoneInfo) -> str | None:
    if not iso:
        return None
    d = datetime.fromisoformat(iso).astimezone(tz)
    today = datetime.now(tz).date()
    if d.date() == today:
        return f"today at {d:%H:%M}"
    if (today - d.date()).days == 1:
        return f"yesterday at {d:%H:%M}"
    return f"{d:%a %-d %b} at {d:%H:%M}"


def _minutes(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    return f"{m} minute{'s' if m != 1 else ''}"


def _ordinal(n) -> str:
    if n is None:
        return "–"
    n = int(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
