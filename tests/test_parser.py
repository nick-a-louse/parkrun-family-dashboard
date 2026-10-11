from datetime import date
from pathlib import Path

import pytest

from app.parser import (ParseError, distance_for, format_time, page_kind, parse_event,
                        parse_profile, parse_time)

FIX = Path(__file__).parent / "fixtures"


def read(name):
    return (FIX / name).read_text()


def test_times():
    assert parse_time("12:45") == 765
    assert parse_time("1:02:03") == 3723
    assert parse_time("00:12:45") == 765
    assert format_time(765) == "12:45"
    assert format_time(3723) == "1:02:03"


def test_distance():
    assert distance_for("riverside-juniors") == "2k"
    assert distance_for("riverside") == "5k"


def test_junior_profile():
    p = parse_profile(read("profile_junior.html"))
    assert p.athlete_id == 1000001
    assert p.name == "Ava TESTRUNNER"
    assert p.total_junior == 3 and p.total_5k == 0
    assert len(p.results) == 3
    first = p.results[0]
    assert first.event_slug == "riverside-juniors"
    assert first.event_name == "Riverside juniors"
    assert first.run_date == date(2026, 10, 11)
    assert first.run_number == 573
    assert first.position == 61
    assert first.time_seconds == 12 * 60 + 45
    assert first.age_grade == 49.83
    assert first.parkrun_pb is True
    assert p.results[1].parkrun_pb is False
    assert first.distance == "2k"


def test_empty_profile():
    p = parse_profile(read("profile_empty.html"))
    assert p.athlete_id == 1000003
    assert p.results == []
    assert p.total_5k is None and p.total_junior is None


def test_mixed_profile():
    p = parse_profile(read("profile_mixed.html"))
    assert p.total_5k == 1 and p.total_junior == 4
    dists = [r.distance for r in p.results]
    assert dists.count("5k") == 1 and dists.count("2k") == 4
    assert p.results[0].age_grade == 48.10   # "AgeGrade" header variant


def test_event_page():
    ev = parse_event(read("event_junior.html"))
    assert ev.event_name == "Riverside junior parkrun"
    assert ev.event_slug == "riverside-juniors"
    assert ev.run_date == date(2026, 10, 11)
    assert ev.run_number == 573
    assert ev.finishers == 109
    ava = ev.for_athlete(1000001)
    assert ava.gender == "Female"
    assert (ava.gender_position, ava.gender_count) == (25, 46)
    assert ava.age_group == "JW10"
    assert ava.position == 61
    max_ = ev.for_athlete(1000002)
    assert (max_.gender_position, max_.gender_count) == (30, 61)
    unknown = [r for r in ev.rows if r.athlete_id is None]
    assert unknown and unknown[0].time_seconds is None


def test_page_kind():
    assert page_kind(read("profile_junior.html")) == "profile"
    assert page_kind(read("event_junior.html")) == "event"
    assert page_kind("<html><body>hello</body></html>") == "unknown"


def test_not_a_profile():
    with pytest.raises(ParseError):
        parse_profile("<html><body><h1>Nope</h1></body></html>")
