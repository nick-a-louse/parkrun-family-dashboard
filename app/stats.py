"""Derived figures: PBs, streaks, milestones and chart series."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import Runner
from .db import Database
from .parser import format_time

DISTANCES = ["5k", "2k"]
DISTANCE_LABEL = {"5k": "5k parkrun", "2k": "2k junior parkrun"}
DISTANCE_KM = {"5k": 5.0, "2k": 2.0}


def format_pace(seconds_per_km: float | None) -> str:
    """e.g. 380.4 -> '6:20 /km'"""
    if seconds_per_km is None:
        return ""
    return f"{format_time(round(seconds_per_km))} /km"

# parkrun milestone clubs. Junior parkrun bands are named after the distance covered.
MILESTONES = {
    "2k": [(11, "Half marathon"), (21, "Marathon"), (50, "Ultra marathon"), (100, "100"), (250, "250")],
    "5k": [(25, "25"), (50, "50"), (100, "100"), (250, "250"), (500, "500"), (1000, "1000")],
}
JUNIOR_5K_MILESTONE = (10, "10")   # the 10 milestone at 5k is for under-18s only


def milestone_list(distance: str, junior: bool) -> list[tuple[int, str]]:
    ms = list(MILESTONES[distance])
    if distance == "5k" and junior:
        ms.insert(0, JUNIOR_5K_MILESTONE)
    return ms


def milestone_name(distance: str, n: int, label: str) -> str:
    if distance == "2k":
        return f"{label} ({n})" if label != str(n) else f"{n} junior parkruns"
    return f"{n} club"


def weekend_of(d: date) -> date:
    """The Saturday of the weekend a run belongs to (Sunday juniors -> Saturday before)."""
    return d - timedelta(days=(d.weekday() - 5) % 7)


@dataclass
class Result:
    athlete_id: int
    runner: str
    event_slug: str
    event_name: str
    run_number: int
    distance: str
    run_date: date
    position: int | None
    time_seconds: int
    age_grade: float | None
    gender: str | None
    gender_position: int | None
    gender_count: int | None
    finishers: int | None
    age_group: str | None
    parkrun_pb: bool
    # derived
    is_first: bool = False          # first run at this distance
    is_pb: bool = False             # new best at this distance (not counting the first run)
    is_event_pb: bool = False       # new best at this event (not counting the first visit)
    run_count: int = 0              # nth run at this distance
    milestone: str | None = None    # milestone reached with this run

    @property
    def time(self) -> str:
        return format_time(self.time_seconds)

    @property
    def pace_seconds(self) -> float:
        """Average time per kilometre for this run."""
        return self.time_seconds / DISTANCE_KM[self.distance]

    @property
    def pace(self) -> str:
        return format_pace(self.pace_seconds)

    @property
    def url(self) -> str:
        return f"https://www.parkrun.org.uk/{self.event_slug}/results/{self.run_number}/"

    def as_dict(self) -> dict:
        return {
            "runner": self.runner,
            "parkrun_id": f"A{self.athlete_id}",
            "date": self.run_date.isoformat(),
            "event": self.event_name,
            "event_slug": self.event_slug,
            "run_number": self.run_number,
            "distance": self.distance,
            "time": self.time,
            "pace": format_time(round(self.pace_seconds)),
            "pace_seconds_per_km": round(self.pace_seconds, 1),
            "time_seconds": self.time_seconds,
            "position": self.position,
            "finishers": self.finishers,
            "gender": self.gender,
            "gender_position": self.gender_position,
            "gender_count": self.gender_count,
            "age_group": self.age_group,
            "age_grade": self.age_grade,
            "new_pb": self.is_pb,
            "first_at_distance": self.is_first,
            "event_pb": self.is_event_pb,
            "milestone": self.milestone,
            "results_url": self.url,
        }


@dataclass
class Best:
    time_seconds: int
    run_date: date
    event_name: str
    distance: str = "5k"

    @property
    def pace(self):
        return format_pace(self.time_seconds / DISTANCE_KM[self.distance])

    @property
    def time(self):
        return format_time(self.time_seconds)


@dataclass
class DistanceSummary:
    distance: str
    runs: int
    pb: Best | None
    year_best: Best | None
    event_pbs: list[tuple[str, Best, int]]          # event name, best, runs there
    milestones_reached: list[str]
    next_milestone: tuple[str, int] | None          # (name, runs to go)
    avg_pace: float | None                          # mean seconds per km over all runs
    latest_age_grade: float | None
    best_age_grade: float | None
    best_position: int | None
    best_gender_position: int | None


@dataclass
class PersonSummary:
    athlete_id: int
    name: str
    colour: str
    distances: list[DistanceSummary] = field(default_factory=list)
    current_streak: int = 0
    longest_streak: int = 0
    last_run: date | None = None
    total_runs: int = 0


def load_results(db: Database, runners: list[Runner]) -> list[Result]:
    names = {r.athlete_id: r.name for r in runners}
    rows = db.query("SELECT * FROM results ORDER BY run_date ASC, athlete_id ASC")
    results = []
    for r in rows:
        if r["athlete_id"] not in names:
            continue  # removed from the config: hide
        results.append(Result(
            athlete_id=r["athlete_id"], runner=names[r["athlete_id"]], event_slug=r["event_slug"],
            event_name=r["event_name"], run_number=r["run_number"], distance=r["distance"],
            run_date=date.fromisoformat(r["run_date"]), position=r["position"],
            time_seconds=r["time_seconds"], age_grade=r["age_grade"], gender=r["gender"],
            gender_position=r["gender_position"], gender_count=r["gender_count"],
            finishers=r["finishers"], age_group=r["age_group"], parkrun_pb=bool(r["parkrun_pb"]),
        ))
    annotate(results, _juniors(db))
    return results


def _juniors(db: Database) -> set[int]:
    """Runners whose latest age category is a junior one (JM10, JW11-14, ...)."""
    return {r["athlete_id"] for r in db.query("SELECT athlete_id, age_group FROM runners")
            if (r["age_group"] or "").upper().startswith("J")}


def annotate(results: list[Result], juniors: set[int] = frozenset()) -> None:
    """Mark first runs, PBs and milestones, walking each runner's history in date order."""
    best: dict[tuple, int] = {}
    event_best: dict[tuple, int] = {}
    count: dict[tuple, int] = defaultdict(int)
    for r in sorted(results, key=lambda x: (x.run_date, x.athlete_id)):
        k = (r.athlete_id, r.distance)
        count[k] += 1
        r.run_count = count[k]
        if k not in best:
            r.is_first = True
            best[k] = r.time_seconds
        elif r.time_seconds < best[k]:
            r.is_pb = True
            best[k] = r.time_seconds
        ek = (r.athlete_id, r.event_slug)
        if ek in event_best and r.time_seconds < event_best[ek]:
            r.is_event_pb = True
        event_best[ek] = min(event_best.get(ek, r.time_seconds), r.time_seconds)
        for n, label in milestone_list(r.distance, r.athlete_id in juniors):
            if r.run_count == n:
                r.milestone = milestone_name(r.distance, n, label)


def streaks(dates: list[date], today: date | None = None) -> tuple[int, int]:
    """(current, longest) runs of consecutive weekends with at least one result."""
    if not dates:
        return 0, 0
    weeks = sorted({weekend_of(d) for d in dates})
    longest = run = 1
    for a, b in zip(weeks, weeks[1:]):
        run = run + 1 if (b - a).days == 7 else 1
        longest = max(longest, run)
    today = today or date.today()
    this_weekend = weekend_of(today)
    # Still "current" if the last run was this weekend or last weekend
    current = run if (this_weekend - weeks[-1]).days <= 7 else 0
    return current, longest


def summarise(db: Database, runners: list[Runner], results: list[Result], palette: list[str],
              today: date | None = None) -> list[PersonSummary]:
    today = today or date.today()
    juniors = _juniors(db)
    totals = {r["athlete_id"]: r for r in db.query("SELECT * FROM runners")}
    people = []
    for i, runner in enumerate(runners):
        mine = [r for r in results if r.athlete_id == runner.athlete_id]
        cur, longest = streaks([r.run_date for r in mine], today)
        p = PersonSummary(
            athlete_id=runner.athlete_id, name=runner.name,
            colour=runner.colour or palette[i % len(palette)],
            current_streak=cur, longest_streak=longest,
            last_run=max((r.run_date for r in mine), default=None),
            total_runs=len(mine),
        )
        t = totals.get(runner.athlete_id)
        for dist in DISTANCES:
            runs = [r for r in mine if r.distance == dist]
            official = None
            if t is not None:
                official = t["total_junior"] if dist == "2k" else t["total_5k"]
            n = max(official or 0, len(runs))
            if not runs and not n:
                continue
            pb = min(runs, key=lambda r: (r.time_seconds, r.run_date), default=None)
            this_year = [r for r in runs if r.run_date.year == today.year]
            yb = min(this_year, key=lambda r: (r.time_seconds, r.run_date), default=None)
            by_event: dict[str, list[Result]] = defaultdict(list)
            for r in runs:
                by_event[r.event_name].append(r)
            event_pbs = []
            for name, rs in sorted(by_event.items(), key=lambda kv: -len(kv[1])):
                b = min(rs, key=lambda r: (r.time_seconds, r.run_date))
                event_pbs.append((name, Best(b.time_seconds, b.run_date, b.event_name, dist), len(rs)))
            ms = milestone_list(dist, runner.athlete_id in juniors)
            reached = [milestone_name(dist, m, label) for m, label in ms if n >= m]
            nxt = next(((milestone_name(dist, m, label), m - n) for m, label in ms if n < m), None)
            ags = [r.age_grade for r in runs if r.age_grade]
            positions = [r.position for r in runs if r.position]
            gpositions = [r.gender_position for r in runs if r.gender_position]
            latest = max(runs, key=lambda r: r.run_date, default=None)
            p.distances.append(DistanceSummary(
                distance=dist, runs=n,
                pb=Best(pb.time_seconds, pb.run_date, pb.event_name, dist) if pb else None,
                year_best=Best(yb.time_seconds, yb.run_date, yb.event_name, dist) if yb else None,
                event_pbs=event_pbs, milestones_reached=reached, next_milestone=nxt,
                avg_pace=(sum(r.pace_seconds for r in runs) / len(runs)) if runs else None,
                latest_age_grade=latest.age_grade if latest else None,
                best_age_grade=max(ags) if ags else None,
                best_position=min(positions) if positions else None,
                best_gender_position=min(gpositions) if gpositions else None,
            ))
        people.append(p)
    return people


def chart_series(results: list[Result], people: list[PersonSummary]) -> dict:
    """{distance: [{name, colour, points: [{date, seconds, time, event, age_grade, pb}]}]}"""
    colours = {p.athlete_id: (p.name, p.colour) for p in people}
    out = {}
    for dist in DISTANCES:
        series = []
        for aid, (name, colour) in colours.items():
            pts = [{
                "date": r.run_date.isoformat(), "seconds": r.time_seconds, "time": r.time, "pace": r.pace,
                "event": r.event_name, "age_grade": r.age_grade, "pb": r.is_pb or r.is_first,
            } for r in results if r.athlete_id == aid and r.distance == dist]
            if pts:
                series.append({"name": name, "colour": colour, "points": pts})
        out[dist] = series
    return out
