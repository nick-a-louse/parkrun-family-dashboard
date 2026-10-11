from datetime import date

from app.config import load_runners, load_settings
from app.db import Database
from app.fetcher import FetchBlocked
from app.stats import load_results, streaks, summarise
from app.sync import Syncer

from conftest import FakeFetcher, FakeNotifier


def make(env, pages, notifier=None):
    s = load_settings()
    runners = load_runners(s.runners_file)
    db = Database(s.db_path)
    fetcher = FakeFetcher(pages)
    syncer = Syncer(s, db, runners, fetcher_factory=lambda: fetcher, notifier=notifier)
    return s, db, runners, syncer, fetcher


def base_pages(fx):
    return {
        "/parkrunner/1000003/all/": fx["profile_empty.html"],
        "/parkrunner/1000001/all/": fx["profile_junior.html"],
        "/parkrunner/1000002/all/": fx["profile_mixed.html"],
        "/riverside-juniors/results/573/": fx["event_junior.html"],
        "/riverside-juniors/results/572/": fx["event_junior.html"].replace("#573", "#572"),
    }


def test_first_sync_backfills_history_without_emailing(env, fixtures):
    n = FakeNotifier()
    s, db, runners, syncer, fetcher = make(env, base_pages(fixtures), n)
    out = syncer.sync("startup")
    assert out.new_results == 8              # 3 for Ava + 5 for Max, none for Sam
    count = db.one("SELECT COUNT(*) AS n FROM results")["n"]
    assert count == 8
    assert n.sent == []                      # history isn't news
    # Event page 573 has both children: gender position filled from one request
    ava = db.one("SELECT * FROM results WHERE athlete_id=1000001 AND run_number=573")
    assert (ava["gender_position"], ava["gender_count"], ava["finishers"]) == (25, 46, 109)
    maxr = db.one("SELECT * FROM results WHERE athlete_id=1000002 AND run_number=573")
    assert maxr["gender_position"] == 30
    assert fetcher.paths.count("/riverside-juniors/results/573/") == 1
    # pages we couldn't fetch (404 in the fake) are reported, not fatal
    assert any("404" in e for e in out.errors)


def test_resync_is_idempotent_and_new_results_email(env, fixtures):
    n = FakeNotifier()
    pages = base_pages(fixtures)
    s, db, runners, syncer, fetcher = make(env, pages, n)
    syncer.sync("startup")
    again = syncer.sync("schedule")
    assert again.new_results == 0
    assert db.one("SELECT COUNT(*) AS n FROM results")["n"] == 8

    # Ava runs again the next weekend
    newer = fixtures["profile_junior.html"].replace("3 junior parkruns total", "4 junior parkruns total").replace(
        '<tbody><tr class=" even"><td><a href="https://www.parkrun.org.uk/riverside-juniors/results/">',
        '<tbody><tr><td><a href="https://www.parkrun.org.uk/riverside-juniors/results/">Riverside juniors</a></td>'
        '<td><a href="https://www.parkrun.org.uk/riverside-juniors/results/574/"><span class="format-date">18/10/2026</span></a></td>'
        '<td>574</td><td>40</td><td>12:10</td><td>52.10%</td><td> PB </td></tr>'
        '<tr class=" even"><td><a href="https://www.parkrun.org.uk/riverside-juniors/results/">', 1)
    pages["/parkrunner/1000001/all/"] = newer
    out = syncer.sync("schedule")
    assert out.new_results == 1
    assert len(n.sent) == 1 and n.sent[0] == [(1000001, "riverside-juniors", 574)]
    # not emailed twice
    syncer.sync("schedule")
    assert len(n.sent) == 1


def test_gap_is_filled_after_outage(env, fixtures):
    pages = base_pages(fixtures)
    s, db, runners, syncer, fetcher = make(env, pages)
    # parkrun unreachable for a while
    for k in list(pages):
        pages[k] = FetchBlocked("HTTP 405")
    out = syncer.sync("schedule")
    assert not out.ok and out.blocked
    assert fetcher.requests == 1             # stops at the first refusal
    assert db.one("SELECT COUNT(*) AS n FROM results")["n"] == 0
    # back online: everything arrives
    pages.update(base_pages(fixtures))
    out = syncer.sync("schedule")
    assert db.one("SELECT COUNT(*) AS n FROM results")["n"] == 8


def test_failure_alert_after_threshold(env, fixtures):
    n = FakeNotifier()
    s, db, runners, syncer, fetcher = make(env, {"/parkrunner/1000003/all/": FetchBlocked("HTTP 405")}, n)
    for _ in range(4):
        syncer.sync("schedule")
    assert len(n.alerts) == 1 and n.alerts[0][0] == 3


def test_manual_import(env, fixtures):
    s, db, runners, syncer, fetcher = make(env, {})
    out = syncer.import_html(fixtures["profile_junior.html"])
    assert out.ok and out.new_results == 3
    out = syncer.import_html(fixtures["event_junior.html"])
    assert out.ok
    assert db.one("SELECT gender_position FROM results WHERE athlete_id=1000001 AND run_number=573")[0] == 25
    bad = syncer.import_html("<html><body>nope</body></html>")
    assert not bad.ok


def test_expected_runners(env, fixtures):
    s, db, runners, syncer, fetcher = make(env, base_pages(fixtures))
    syncer.sync("startup")
    sunday = date(2026, 10, 11)
    saturday = date(2026, 10, 17)
    assert {r.name for r in syncer.expected_on(sunday)} == {"Ava", "Max"}
    assert syncer.all_expected_in(sunday)
    # Sam is configured for Saturdays and has no result yet
    assert {r.name for r in syncer.expected_on(saturday)} >= {"Sam"}
    assert not syncer.all_expected_in(saturday)


def test_stats(env, fixtures):
    s, db, runners, syncer, fetcher = make(env, base_pages(fixtures))
    syncer.sync("startup")
    results = load_results(db, runners)
    ava = sorted([r for r in results if r.runner == "Ava"], key=lambda r: r.run_date)
    assert ava[0].is_first and not ava[0].is_pb
    assert not ava[1].is_pb                       # 13:44 slower than 13:18
    assert ava[2].is_pb                           # 12:45
    people = summarise(db, runners, results, ["#000"], today=date(2026, 10, 12))
    ava_p = next(p for p in people if p.name == "Ava")
    d2 = ava_p.distances[0]
    assert d2.distance == "2k" and d2.runs == 3
    assert d2.pb.time == "12:45"
    assert d2.next_milestone == ("Half marathon (11)", 8)
    assert ava_p.current_streak == 3
    sam = next(p for p in people if p.name == "Sam")
    assert sam.distances == []
    max_p = next(p for p in people if p.name == "Max")
    assert [d.distance for d in max_p.distances] == ["5k", "2k"]
    five = max_p.distances[0]
    # Max is a junior (JM10), so the 10 milestone applies at 5k
    assert five.next_milestone == ("10 club", 9)
    two = max_p.distances[1]
    assert len(two.event_pbs) == 2


def test_streaks():
    d = date
    assert streaks([], d(2026, 10, 11)) == (0, 0)
    # Sat + Sun of the same weekend count once
    assert streaks([d(2026, 10, 10), d(2026, 10, 11)], d(2026, 10, 11)) == (1, 1)
    weeks = [d(2026, 9, 20), d(2026, 9, 27), d(2026, 10, 11)]   # missed 4 Oct
    assert streaks(weeks, d(2026, 10, 11)) == (1, 2)
    assert streaks(weeks, d(2026, 10, 30)) == (0, 2)            # streak lapsed


def test_email_content(env, fixtures, monkeypatch):
    from pathlib import Path
    from app.notify import Notifier
    monkeypatch.setenv("SMTP_HOST", "relay.test")
    monkeypatch.setenv("EMAIL_FROM", "dash@test")
    monkeypatch.setenv("EMAIL_TO", "me@test")
    s, db, runners, syncer, fetcher = make(env, base_pages(fixtures))
    syncer.sync("startup")
    n = Notifier(load_settings(), db, runners, Path(__file__).resolve().parent.parent / "app" / "templates")
    assert n.enabled
    msg = n.build_new_results([(1000001, "riverside-juniors", 573), (1000002, "riverside-juniors", 573)])
    assert msg["To"] == "me@test"
    assert "2 new results" in msg["Subject"]
    text = msg.get_body(("plain",)).get_content()
    html = msg.get_body(("html",)).get_content()
    assert "Ava" in text and "12:45" in text and "25 of 46" in text
    assert "http://gomez.test:5062" in text and 'href="http://gomez.test:5062"' in html


def test_scheduler_jobs(env, fixtures):
    from app.scheduler import describe, start_scheduler
    s, db, runners, syncer, fetcher = make(env, base_pages(fixtures))
    sched = start_scheduler(s, syncer)
    try:
        jobs = describe(sched)
        assert len(jobs) == 6                      # 5 weekend checks + Monday catch-up
        assert all(j["next_text"] for j in jobs)
    finally:
        sched.shutdown(wait=False)
