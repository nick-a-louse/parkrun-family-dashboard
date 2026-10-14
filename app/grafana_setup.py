"""Create (or update) the parkrun alert rules and dashboard in Grafana.

Run inside the container once it's on the same Docker network as Grafana:

    docker exec -e GRAFANA_PASSWORD='...' parkrun python -m app.grafana_setup

Options: --url (default http://grafana:3000), --user (default admin), --dry-run.
Safe to run again: rules and the dashboard are updated in place by their fixed UIDs.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

RULE_GROUP = "Parkrun"
DASHBOARD_UID = "parkrun"
APP_URL = os.environ.get("BASE_URL", "http://your-server:5062")


class Grafana:
    def __init__(self, url: str, user: str, password: str):
        self.url = url.rstrip("/")
        token = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json",
                        "X-Disable-Provenance": "true"}

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw.decode(errors="replace")


def find_datasource(g: Grafana) -> dict:
    status, sources = g.call("GET", "/api/datasources")
    if status != 200:
        raise SystemExit(f"Couldn't list Grafana datasources (HTTP {status}): {sources}")
    prom = [s for s in sources if s.get("type") in ("prometheus", "victoriametrics-datasource")]
    if not prom:
        raise SystemExit("No Prometheus/VictoriaMetrics datasource found in Grafana")
    for s in prom:
        if "victoria" in (s.get("name", "") + s.get("url", "")).lower():
            return s
    return prom[0]


def find_folder(g: Grafana) -> str:
    """Put the rules beside the existing server alerts."""
    status, rules = g.call("GET", "/api/v1/provisioning/alert-rules")
    if status == 200 and rules:
        for r in rules:
            if r.get("ruleGroup") == "Server Alerts":
                return r["folderUID"]
        return rules[0]["folderUID"]
    status, folders = g.call("GET", "/api/folders")
    if status == 200 and folders:
        return folders[0]["uid"]
    status, f = g.call("POST", "/api/folders", {"title": "Gomez"})
    if status not in (200, 201):
        raise SystemExit(f"Couldn't create a folder (HTTP {status}): {f}")
    return f["uid"]


def _query(ds_uid: str, expr: str) -> dict:
    return {"refId": "A", "relativeTimeRange": {"from": 600, "to": 0}, "datasourceUid": ds_uid,
            "model": {"refId": "A", "expr": expr, "instant": True, "range": False,
                      "intervalMs": 1000, "maxDataPoints": 43200}}


def _reduce() -> dict:
    return {"refId": "B", "datasourceUid": "__expr__", "relativeTimeRange": {"from": 0, "to": 0},
            "model": {"refId": "B", "type": "reduce", "expression": "A", "reducer": "last",
                      "settings": {"mode": "dropNN"}}}


def _threshold(op: str, value: float) -> dict:
    return {"refId": "C", "datasourceUid": "__expr__", "relativeTimeRange": {"from": 0, "to": 0},
            "model": {"refId": "C", "type": "threshold", "expression": "B",
                      "conditions": [{"evaluator": {"type": op, "params": [value]}}]}}


def alert_rules(ds_uid: str, folder_uid: str) -> list[dict]:
    def rule(uid, title, expr, op, value, for_, no_data, summary, description):
        return {
            "uid": uid, "title": title, "ruleGroup": RULE_GROUP, "folderUID": folder_uid,
            "condition": "C", "for": for_, "noDataState": no_data, "execErrState": "Error",
            "data": [_query(ds_uid, expr), _reduce(), _threshold(op, value)],
            "annotations": {"summary": summary, "description": description,
                            "runbook_url": APP_URL},
            "labels": {"service": "parkrun"}, "isPaused": False,
        }
    return [
        rule("parkrun-down", "Parkrun Dashboard Down", 'up{job="parkrun"}', "lt", 1, "5m", "Alerting",
             "The parkrun dashboard isn't responding",
             "VictoriaMetrics can't reach the parkrun container. Check `docker ps` and "
             "`docker logs parkrun` on the server; restart with `cd /srv/parkrun && docker compose up -d`."),
        rule("parkrun-failing", "Parkrun Checks Failing", "parkrun_consecutive_failures", "gt", 2, "1m", "OK",
             "3 or more parkrun checks in a row have failed",
             "See 'Recent checks' on the dashboard. If parkrun is refusing requests, switch to the "
             ":browser image with FETCHER=browser, or import saved pages."),
        rule("parkrun-stale", "Parkrun Results Not Updating", "time() - parkrun_last_success_timestamp_seconds",
             "gt", 8 * 86400, "1h", "OK",
             "No successful parkrun check for over 8 days",
             "A whole weekend has passed without a successful check. The scheduler may have stopped; "
             "restart the container and press 'Check for results'."),
    ]


def dashboard(ds_uid: str) -> dict:
    ds = {"type": "prometheus", "uid": ds_uid}

    def stat(pid, title, expr, x, unit=None, mappings=None, thresholds=None, w=6):
        p = {"id": pid, "type": "stat", "title": title, "datasource": ds,
             "gridPos": {"h": 5, "w": w, "x": x, "y": 0},
             "targets": [{"refId": "A", "expr": expr, "instant": True, "datasource": ds}],
             "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                         "colorMode": "background", "graphMode": "none", "textMode": "value"},
             "fieldConfig": {"defaults": {"unit": unit or "none", "mappings": mappings or [],
                                          "thresholds": thresholds or {"mode": "absolute", "steps": [
                                              {"color": "green", "value": None}]}}, "overrides": []}}
        return p

    up_map = [{"type": "value", "options": {"0": {"text": "Down", "color": "red"},
                                             "1": {"text": "Up", "color": "green"}}}]
    return {
        "uid": DASHBOARD_UID, "title": "Parkrun dashboard", "tags": ["parkrun", "gomez"],
        "timezone": "browser", "schemaVersion": 39, "refresh": "5m",
        "time": {"from": "now-14d", "to": "now"},
        "links": [{"title": "Open the parkrun dashboard", "type": "link", "url": APP_URL, "targetBlank": True}],
        "panels": [
            stat(1, "Status", 'up{job="parkrun"}', 0, mappings=up_map,
                 thresholds={"mode": "absolute", "steps": [{"color": "red", "value": None},
                                                           {"color": "green", "value": 1}]}),
            stat(2, "Last successful check", "time() - parkrun_last_success_timestamp_seconds", 6,
                 unit="dtdurations",
                 thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                           {"color": "orange", "value": 7 * 86400},
                                                           {"color": "red", "value": 8 * 86400}]}),
            stat(3, "Failed checks in a row", "parkrun_consecutive_failures", 12,
                 thresholds={"mode": "absolute", "steps": [{"color": "green", "value": None},
                                                           {"color": "orange", "value": 1},
                                                           {"color": "red", "value": 3}]}),
            stat(4, "Next scheduled check", "parkrun_next_check_timestamp_seconds * 1000", 18,
                 unit="dateTimeFromNow"),
            {"id": 5, "type": "timeseries", "title": "Results stored", "datasource": ds,
             "gridPos": {"h": 8, "w": 12, "x": 0, "y": 5},
             "targets": [{"refId": "A", "expr": "parkrun_results", "legendFormat": "{{runner}} {{distance}}",
                          "datasource": ds}],
             "fieldConfig": {"defaults": {"unit": "none", "decimals": 0,
                                          "custom": {"lineInterpolation": "stepAfter"}}, "overrides": []}},
            {"id": 6, "type": "timeseries", "title": "Checks per hour", "datasource": ds,
             "gridPos": {"h": 8, "w": 12, "x": 12, "y": 5},
             "targets": [{"refId": "A", "expr": "sum by (result) (increase(parkrun_checks_total[1h]))",
                          "legendFormat": "{{result}}", "datasource": ds}],
             "fieldConfig": {"defaults": {"unit": "none", "decimals": 0,
                                          "custom": {"drawStyle": "bars", "fillOpacity": 60}},
                             "overrides": [
                                 {"matcher": {"id": "byName", "options": "failed"},
                                  "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]},
                                 {"matcher": {"id": "byName", "options": "ok"},
                                  "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]}]}},
        ],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("GRAFANA_URL", "http://grafana:3000"))
    ap.add_argument("--user", default=os.environ.get("GRAFANA_USER", "admin"))
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent and stop")
    args = ap.parse_args(argv)

    if args.dry_run:
        print(json.dumps({"rules": alert_rules("<datasource>", "<folder>"),
                          "dashboard": dashboard("<datasource>")}, indent=2))
        return 0
    password = os.environ.get("GRAFANA_PASSWORD")
    if not password:
        print("Set GRAFANA_PASSWORD (the Grafana admin password).", file=sys.stderr)
        return 2
    g = Grafana(args.url, args.user, password)
    status, health = g.call("GET", "/api/health")
    if status != 200:
        print(f"Can't reach Grafana at {args.url} (HTTP {status}). Is this container on the Grafana network?",
              file=sys.stderr)
        return 1
    status, _ = g.call("GET", "/api/user")
    if status == 401:
        print("Grafana rejected the login. Check GRAFANA_USER / GRAFANA_PASSWORD.", file=sys.stderr)
        return 1

    ds = find_datasource(g)
    folder = find_folder(g)
    print(f"Datasource: {ds['name']} ({ds['uid']}); folder: {folder}")

    for r in alert_rules(ds["uid"], folder):
        status, _ = g.call("GET", f"/api/v1/provisioning/alert-rules/{r['uid']}")
        if status == 200:
            status, body = g.call("PUT", f"/api/v1/provisioning/alert-rules/{r['uid']}", r)
            verb = "Updated"
        else:
            status, body = g.call("POST", "/api/v1/provisioning/alert-rules", r)
            verb = "Created"
        if status not in (200, 201):
            print(f"Failed on alert '{r['title']}' (HTTP {status}): {body}", file=sys.stderr)
            return 1
        print(f"{verb} alert: {r['title']}")

    status, body = g.call("POST", "/api/dashboards/db",
                          {"dashboard": dashboard(ds["uid"]), "folderUid": folder, "overwrite": True,
                           "message": "parkrun dashboard (app.grafana_setup)"})
    if status != 200:
        print(f"Failed to save the dashboard (HTTP {status}): {body}", file=sys.stderr)
        return 1
    print(f"Saved dashboard: {args.url.replace('http://grafana:3000', 'http://your-server:3003')}{body.get('url', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
