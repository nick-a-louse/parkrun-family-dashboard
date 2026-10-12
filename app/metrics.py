"""Prometheus-format metrics at /metrics, for VictoriaMetrics/Grafana alerting."""
from __future__ import annotations

from datetime import datetime, timezone

from . import __version__
from .config import Runner
from .db import Database


def _ts(iso: str | None) -> float | None:
    if not iso:
        return None
    d = datetime.fromisoformat(iso)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.timestamp()


def _label(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render(db: Database, runners: list[Runner], running: bool, next_run: datetime | None) -> str:
    lines: list[str] = []

    def metric(name, help_, type_, samples):
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} {type_}")
        for labels, value in samples:
            if value is None:
                continue
            lab = "{" + ",".join(f'{k}="{_label(v)}"' for k, v in labels.items()) + "}" if labels else ""
            val = f"{value:.3f}" if isinstance(value, float) else str(int(value))
            lines.append(f"{name}{lab} {val}")

    last = db.one("SELECT * FROM fetch_log WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1")
    counts = {r["ok"]: r["n"] for r in db.query(
        "SELECT ok, COUNT(*) AS n FROM fetch_log WHERE finished_at IS NOT NULL AND trigger != 'import' GROUP BY ok")}

    metric("parkrun_build_info", "Build information (gomez app standard name).", "gauge", [({"version": __version__}, 1)])
    metric("parkrun_info", "Build information (kept for existing Grafana panels).", "gauge", [({"version": __version__}, 1)])
    metric("parkrun_last_success_timestamp_seconds", "When the last fully successful check finished (Unix time).",
           "gauge", [({}, _ts(db.get_state("last_success")))])
    metric("parkrun_last_check_timestamp_seconds", "When the last check finished (Unix time).",
           "gauge", [({}, _ts(last["finished_at"]) if last else None)])
    metric("parkrun_last_check_ok", "1 if the last check succeeded, 0 if it failed.",
           "gauge", [({}, last["ok"] if last else None)])
    metric("parkrun_consecutive_failures", "Failed checks in a row (0 after any success).",
           "gauge", [({}, int(db.get_state("consecutive_failures", "0")))])
    metric("parkrun_checks_total", "Checks finished since the database was created.", "counter",
           [({"result": "ok"}, counts.get(1, 0)), ({"result": "failed"}, counts.get(0, 0))])
    metric("parkrun_check_running", "1 while a check is in progress.", "gauge", [({}, int(running))])
    metric("parkrun_next_check_timestamp_seconds", "When the next scheduled check is due (Unix time).",
           "gauge", [({}, next_run.timestamp() if next_run else None)])

    names = {r.athlete_id: r.name for r in runners}
    res = db.query("SELECT athlete_id, distance, COUNT(*) AS n, MAX(run_date) AS last FROM results "
                   "GROUP BY athlete_id, distance")
    metric("parkrun_results", "Stored results per runner and distance.", "gauge",
           [({"runner": names[r["athlete_id"]], "distance": r["distance"]}, r["n"])
            for r in res if r["athlete_id"] in names])
    metric("parkrun_last_result_timestamp_seconds", "Date of each runner's latest result (Unix time, midnight UTC).",
           "gauge", [({"runner": names[r["athlete_id"]], "distance": r["distance"]},
                      _ts(r["last"] + "T00:00:00+00:00")) for r in res if r["athlete_id"] in names])
    return "\n".join(lines) + "\n"
