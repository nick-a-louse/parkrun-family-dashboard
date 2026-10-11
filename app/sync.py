"""Fetching results and keeping the database up to date.

Every sync re-reads each runner's full results page, so the whole history is
loaded on the first run and any gap (container down for a weekend, a failed
fetch) is filled by the next successful sync. Results are upserted, never
duplicated.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from .config import Runner, Settings
from .db import Database, now_iso
from .fetcher import FetchBlocked, FetchError, Fetcher, make_fetcher
from .parser import EventResults, ParseError, Profile, page_kind, parse_event, parse_profile

log = logging.getLogger(__name__)

MAX_EVENT_PAGES_PER_SYNC = 8   # keeps a first back-fill of a long history polite


@dataclass
class SyncOutcome:
    ok: bool = True
    new_results: int = 0
    requests: int = 0
    errors: list[str] = field(default_factory=list)
    blocked: bool = False
    message: str = ""
    skipped: bool = False


class Syncer:
    def __init__(self, settings: Settings, db: Database, runners: list[Runner],
                 fetcher_factory: Callable[[], Fetcher] | None = None,
                 notifier=None):
        self.settings = settings
        self.db = db
        self.runners = runners
        self.fetcher_factory = fetcher_factory or (
            lambda: make_fetcher(settings.fetcher, settings.parkrun_domain, settings.request_delay))
        self.notifier = notifier
        self._lock = threading.Lock()
        self.running = False
        self.sync_runners_table()

    # ------------------------------------------------------------------ setup
    def sync_runners_table(self):
        with self.db.tx() as c:
            for r in self.runners:
                c.execute(
                    "INSERT INTO runners(athlete_id, name) VALUES(?, ?) "
                    "ON CONFLICT(athlete_id) DO UPDATE SET name = excluded.name",
                    (r.athlete_id, r.name))

    def runner(self, athlete_id: int) -> Runner | None:
        return next((r for r in self.runners if r.athlete_id == athlete_id), None)

    # ------------------------------------------------------------- main sync
    def sync(self, trigger: str = "manual", catchup: bool = False) -> SyncOutcome:
        if not self._lock.acquire(blocking=False):
            return SyncOutcome(ok=True, skipped=True, message="A fetch is already running")
        self.running = True
        log_id = self.db.execute("INSERT INTO fetch_log(started_at, trigger) VALUES(?, ?)",
                                 (now_iso(), trigger)).lastrowid
        out = SyncOutcome()
        fetcher = None
        try:
            fetcher = self.fetcher_factory()
            for runner in self.runners:
                try:
                    html = fetcher.profile(runner.athlete_id)
                    profile = parse_profile(html)
                    if profile.athlete_id != runner.athlete_id:
                        raise ParseError(f"Page was for A{profile.athlete_id}, expected {runner.parkrun_id}")
                    out.new_results += self.store_profile(runner, profile)
                except FetchBlocked as e:
                    out.blocked = True
                    out.errors.append(f"{runner.name}: {e}")
                    break  # no point hammering a firewall that's saying no
                except (FetchError, ParseError) as e:
                    out.errors.append(f"{runner.name}: {e}")
            if not out.blocked:
                try:
                    self.fill_event_details(fetcher, catchup=catchup)
                except FetchBlocked as e:
                    out.blocked = True
                    out.errors.append(f"Event results: {e}")
                except (FetchError, ParseError) as e:
                    out.errors.append(f"Event results: {e}")
        except Exception as e:  # unexpected: record and carry on
            log.exception("Sync failed")
            out.errors.append(f"Unexpected error: {e}")
        finally:
            if fetcher is not None:
                out.requests = fetcher.requests
                try:
                    fetcher.close()
                except Exception:
                    pass
        out.ok = not out.errors
        out.message = "; ".join(out.errors) if out.errors else (
            f"{out.new_results} new result{'s' if out.new_results != 1 else ''}")
        self.db.execute(
            "UPDATE fetch_log SET finished_at=?, ok=?, new_results=?, requests=?, message=? WHERE id=?",
            (now_iso(), int(out.ok), out.new_results, out.requests, out.message, log_id))
        if out.ok:
            self.db.set_state("last_success", now_iso())
        self._after_sync(out)
        self.running = False
        self._lock.release()
        return out

    # ------------------------------------------------------------- storage
    def store_profile(self, runner: Runner, profile: Profile) -> int:
        """Upsert a runner's results. Returns how many results were new."""
        initialised = self.db.get_state(f"initialised:{runner.athlete_id}") == "1"
        now = now_iso()
        new = 0
        seen = set()
        with self.db.tx() as c:
            c.execute("UPDATE runners SET parkrun_name=?, total_5k=?, total_junior=?, updated_at=? "
                      "WHERE athlete_id=?",
                      (profile.name, profile.total_5k, profile.total_junior, now, runner.athlete_id))
            for r in profile.results:
                key = (runner.athlete_id, r.event_slug, r.run_number)
                seen.add(key)
                exists = c.execute("SELECT 1 FROM results WHERE athlete_id=? AND event_slug=? AND run_number=?",
                                   key).fetchone()
                if exists:
                    c.execute(
                        "UPDATE results SET event_name=?, distance=?, run_date=?, position=?, time_seconds=?, "
                        "age_grade=?, parkrun_pb=? WHERE athlete_id=? AND event_slug=? AND run_number=?",
                        (r.event_name, r.distance, r.run_date.isoformat(), r.position, r.time_seconds,
                         r.age_grade, int(r.parkrun_pb), *key))
                else:
                    new += 1
                    c.execute(
                        "INSERT INTO results(athlete_id, event_slug, run_number, event_name, distance, run_date, "
                        "position, time_seconds, age_grade, parkrun_pb, first_seen_at, notified) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (*key, r.event_name, r.distance, r.run_date.isoformat(), r.position,
                         r.time_seconds, r.age_grade, int(r.parkrun_pb), now,
                         0 if initialised else 1))  # first load = history, don't email it
            # parkrun occasionally removes a result (e.g. a correction). Only trust the
            # page for removals when its run counts agree with the rows we parsed.
            expected = (profile.total_5k or 0) + (profile.total_junior or 0)
            if profile.results and expected == len(profile.results):
                for row in c.execute("SELECT event_slug, run_number FROM results WHERE athlete_id=?",
                                     (runner.athlete_id,)).fetchall():
                    if (runner.athlete_id, row["event_slug"], row["run_number"]) not in seen:
                        c.execute("DELETE FROM results WHERE athlete_id=? AND event_slug=? AND run_number=?",
                                  (runner.athlete_id, row["event_slug"], row["run_number"]))
        self.db.set_state(f"initialised:{runner.athlete_id}", "1")
        return new

    def fill_event_details(self, fetcher: Fetcher, catchup: bool = False) -> int:
        """Read event results pages for gender position, finishers and age group.

        Newest first, one page per event (both children at the same junior parkrun
        share a single request), capped per sync. On the Monday catch-up the last
        few days' pages are re-read in case provisional results were corrected.
        """
        recent = (date.today() - timedelta(days=3)).isoformat()
        rows = self.db.query(
            "SELECT DISTINCT event_slug, run_number, MAX(run_date) AS d FROM results "
            "WHERE event_checked_at IS NULL OR (? AND run_date >= ?) "
            "GROUP BY event_slug, run_number ORDER BY d DESC LIMIT ?",
            (int(catchup), recent, MAX_EVENT_PAGES_PER_SYNC))
        done = 0
        errors = []
        for row in rows:
            try:
                html = fetcher.event(row["event_slug"], row["run_number"])
                self.store_event(parse_event(html), row["event_slug"], row["run_number"])
                done += 1
            except FetchBlocked:
                raise
            except (FetchError, ParseError) as e:   # one bad page shouldn't stop the others
                errors.append(f"{row['event_slug']} #{row['run_number']}: {e}")
        if errors:
            raise FetchError("; ".join(errors))
        return done

    def store_event(self, ev: EventResults, slug: str | None = None, run_number: int | None = None) -> int:
        slug = slug or ev.event_slug
        run_number = run_number or ev.run_number
        if not slug or not run_number:
            raise ParseError("Couldn't tell which event this results page is for")
        now = now_iso()
        updated = 0
        with self.db.tx() as c:
            for res in c.execute("SELECT athlete_id FROM results WHERE event_slug=? AND run_number=?",
                                 (slug, run_number)).fetchall():
                row = ev.for_athlete(res["athlete_id"])
                if row is None:
                    c.execute("UPDATE results SET finishers=COALESCE(?, finishers), event_checked_at=? "
                              "WHERE athlete_id=? AND event_slug=? AND run_number=?",
                              (ev.finishers, now, res["athlete_id"], slug, run_number))
                    continue
                c.execute(
                    "UPDATE results SET gender=?, gender_position=?, gender_count=?, finishers=?, age_group=?, "
                    "position=COALESCE(?, position), age_grade=COALESCE(?, age_grade), event_checked_at=? "
                    "WHERE athlete_id=? AND event_slug=? AND run_number=?",
                    (row.gender, row.gender_position, row.gender_count, ev.finishers, row.age_group,
                     row.position, row.age_grade, now, res["athlete_id"], slug, run_number))
                c.execute("UPDATE runners SET gender=COALESCE(?, gender), age_group=COALESCE(?, age_group) "
                          "WHERE athlete_id=?", (row.gender, row.age_group, res["athlete_id"]))
                updated += 1
        return updated

    # -------------------------------------------------------------- import
    def import_html(self, html: str) -> SyncOutcome:
        """Manual fallback: store a saved profile or event results page."""
        out = SyncOutcome()
        log_id = self.db.execute("INSERT INTO fetch_log(started_at, trigger) VALUES(?, 'import')",
                                 (now_iso(),)).lastrowid
        try:
            kind = page_kind(html)
            if kind == "profile":
                profile = parse_profile(html)
                runner = self.runner(profile.athlete_id)
                if runner is None:
                    raise ParseError(f"A{profile.athlete_id} isn't in the runner list")
                out.new_results = self.store_profile(runner, profile)
                out.message = f"Imported {runner.name}'s results: {out.new_results} new"
            elif kind == "event":
                ev = parse_event(html)
                n = self.store_event(ev)
                out.message = f"Imported {ev.event_name or 'event'} #{ev.run_number}: updated {n} result(s)"
            else:
                raise ParseError("That doesn't look like a parkrunner results page or an event results page")
        except ParseError as e:
            out.ok = False
            out.errors.append(str(e))
            out.message = str(e)
        self.db.execute(
            "UPDATE fetch_log SET finished_at=?, ok=?, new_results=?, message=? WHERE id=?",
            (now_iso(), int(out.ok), out.new_results, out.message, log_id))
        if out.ok:
            self._send_new_results()
        return out

    # ------------------------------------------------------- notifications
    def _after_sync(self, out: SyncOutcome):
        if out.ok:
            self.db.set_state("consecutive_failures", 0)
            self.db.set_state("failure_alert_sent", 0)
        else:
            n = int(self.db.get_state("consecutive_failures", "0")) + 1
            self.db.set_state("consecutive_failures", n)
            if (n >= self.settings.failure_alert_threshold
                    and self.db.get_state("failure_alert_sent", "0") != "1"
                    and self.notifier is not None):
                try:
                    self.notifier.send_failure_alert(n, out.message)
                    self.db.set_state("failure_alert_sent", 1)
                except Exception:
                    log.exception("Could not send failure alert")
        self._send_new_results()

    def _send_new_results(self):
        rows = self.db.query("SELECT athlete_id, event_slug, run_number FROM results WHERE notified = 0")
        if not rows:
            return
        if self.notifier is not None and self.notifier.enabled:
            try:
                self.notifier.send_new_results([tuple(r) for r in rows])
            except Exception:
                log.exception("Could not send new-results email; will retry after the next fetch")
                return
        with self.db.tx() as c:
            c.executemany("UPDATE results SET notified=1 WHERE athlete_id=? AND event_slug=? AND run_number=?",
                          [tuple(r) for r in rows])

    # ------------------------------------------------------------- helpers
    def has_result_on(self, athlete_id: int, day: date) -> bool:
        return self.db.one("SELECT 1 FROM results WHERE athlete_id=? AND run_date=?",
                           (athlete_id, day.isoformat())) is not None

    def expected_on(self, day: date) -> list[Runner]:
        """Runners expected to run on this weekday: from config, else from their history."""
        weekday = day.strftime("%A").lower()
        out = []
        for r in self.runners:
            if r.expect:
                if weekday in r.expect:
                    out.append(r)
                continue
            dates = self.db.query("SELECT DISTINCT run_date FROM results WHERE athlete_id=?", (r.athlete_id,))
            days = {date.fromisoformat(d["run_date"]).strftime("%A").lower() for d in dates}
            if weekday in days:
                out.append(r)
        return out

    def all_expected_in(self, day: date) -> bool:
        expected = self.expected_on(day)
        return bool(expected) and all(self.has_result_on(r.athlete_id, day) for r in expected)
