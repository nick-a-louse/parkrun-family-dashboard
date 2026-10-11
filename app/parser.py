"""Parse parkrun HTML pages.

Two page types are used:

* a runner's full results page:  /parkrunner/<id>/all/
* an event's results page:        /<event>/results/<run number>/

Parsing is deliberately tolerant: it keys off table captions, link URLs and
data- attributes rather than exact layout, so cosmetic changes don't break it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from bs4 import BeautifulSoup


class ParseError(Exception):
    pass


@dataclass
class ProfileResult:
    event_slug: str
    event_name: str
    run_date: date
    run_number: int
    position: int | None
    time_seconds: int
    age_grade: float | None
    parkrun_pb: bool          # parkrun's own "PB" marker (personal best at that event)

    @property
    def distance(self) -> str:
        return distance_for(self.event_slug)


@dataclass
class Profile:
    athlete_id: int
    name: str
    total_5k: int | None
    total_junior: int | None
    results: list[ProfileResult] = field(default_factory=list)


@dataclass
class EventRow:
    athlete_id: int | None
    name: str
    position: int | None
    gender: str | None
    gender_position: int | None
    gender_count: int | None
    age_group: str | None
    age_grade: float | None
    time_seconds: int | None
    runs: int | None


@dataclass
class EventResults:
    event_slug: str | None
    event_name: str | None
    run_date: date | None
    run_number: int | None
    finishers: int | None
    rows: list[EventRow] = field(default_factory=list)

    def for_athlete(self, athlete_id: int) -> EventRow | None:
        for r in self.rows:
            if r.athlete_id == athlete_id:
                return r
        return None


def distance_for(event_slug: str) -> str:
    """Junior parkruns (2k) have slugs ending in -juniors; everything else is 5k."""
    return "2k" if event_slug.lower().endswith("-juniors") else "5k"


def parse_time(text: str) -> int:
    parts = [int(p) for p in text.strip().split(":")]
    if not parts or len(parts) > 3:
        raise ParseError(f"Bad time {text!r}")
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def format_time(seconds: int | None) -> str:
    if seconds is None:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _int(text) -> int | None:
    if text is None:
        return None
    m = re.search(r"\d[\d,]*", str(text))
    return int(m.group(0).replace(",", "")) if m else None


def _pct(text) -> float | None:
    if text is None:
        return None
    m = re.search(r"\d+(?:\.\d+)?", str(text))
    return float(m.group(0)) if m else None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


_SLUG_RE = re.compile(r"https?://[^/]+/([^/]+)/results/(?:(\d+)/?)?")


def page_kind(html: str) -> str:
    """Return 'profile', 'event' or 'unknown' for a saved parkrun page."""
    soup = BeautifulSoup(html, "lxml")
    if soup.select_one("tr.Results-table-row") or soup.select_one(".Results-header"):
        return "event"
    h2 = soup.select_one("#content h2") or soup.find("h2")
    if h2 and re.search(r"\(A\d+\)", h2.get_text()):
        return "profile"
    return "unknown"


def parse_profile(html: str) -> Profile:
    soup = BeautifulSoup(html, "lxml")
    content = soup.select_one("#content") or soup
    h2 = content.find("h2")
    if not h2:
        raise ParseError("No runner heading found; is this a parkrunner results page?")
    heading = _clean(h2.get_text(" "))
    m = re.search(r"\(A(\d+)\)", heading)
    if not m:
        raise ParseError(f"No parkrun ID in heading {heading!r}")
    athlete_id = int(m.group(1))
    name = _clean(heading[: m.start()])

    total_5k = total_junior = None
    for h3 in content.find_all("h3"):
        text = _clean(h3.get_text(" "))
        if "total" not in text:
            continue
        # "3 junior parkruns total", "387 parkruns total", "1 parkrun & 55 junior parkruns total"
        for num, junior in re.findall(r"([\d,]+)\s+(junior\s+)?parkruns?", text):
            if junior:
                total_junior = _int(num)
            else:
                total_5k = _int(num)
    if total_5k is None and total_junior is not None:
        total_5k = 0
    if total_junior is None and total_5k is not None:
        total_junior = 0

    table = None
    for t in content.find_all("table"):
        cap = t.find("caption")
        if cap and re.search(r"all\s+results", cap.get_text(), re.I):
            table = t
            break

    results: list[ProfileResult] = []
    if table is not None:
        headers = [_clean(th.get_text()).lower().replace(" ", "") for th in table.select("thead th")]
        idx = {
            "event": _find(headers, "event", 0),
            "date": _find(headers, "rundate", 1),
            "number": _find(headers, "runnumber", 2),
            "pos": _find(headers, "pos", 3),
            "time": _find(headers, "time", 4),
            "agegrade": _find(headers, "agegrade", 5),
            "pb": _find(headers, "pb?", 6),
        }
        body = table.find("tbody") or table
        for tr in body.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue
            link = None
            for a in tr.find_all("a", href=True):
                if _SLUG_RE.search(a["href"]):
                    link = a["href"]
                    break
            sm = _SLUG_RE.search(link or "")
            if not sm:
                continue
            slug = sm.group(1)
            date_text = _clean(tds[idx["date"]].get_text())
            try:
                run_date = datetime.strptime(date_text, "%d/%m/%Y").date()
            except ValueError as e:
                raise ParseError(f"Bad run date {date_text!r}") from e
            results.append(ProfileResult(
                event_slug=slug,
                event_name=_clean(tds[idx["event"]].get_text()),
                run_date=run_date,
                run_number=_int(tds[idx["number"]].get_text()) or _int(sm.group(2)) or 0,
                position=_int(tds[idx["pos"]].get_text()),
                time_seconds=parse_time(_clean(tds[idx["time"]].get_text())),
                age_grade=_pct(tds[idx["agegrade"]].get_text()) if idx["agegrade"] < len(tds) else None,
                parkrun_pb="pb" in tds[idx["pb"]].get_text().lower() if idx["pb"] < len(tds) else False,
            ))
    elif total_5k or total_junior:
        raise ParseError("Runner has results but the 'All Results' table was not found")

    return Profile(athlete_id=athlete_id, name=name, total_5k=total_5k,
                   total_junior=total_junior, results=results)


def _find(headers: list[str], key: str, default: int) -> int:
    for i, h in enumerate(headers):
        if h.startswith(key):
            return i
    return default


def parse_event(html: str) -> EventResults:
    soup = BeautifulSoup(html, "lxml")
    header = soup.select_one(".Results-header")
    event_name = run_date = run_number = finishers = None
    if header:
        h1 = header.find("h1")
        event_name = _clean(h1.get_text()) if h1 else None
        d = header.select_one(".format-date")
        if d:
            try:
                run_date = date.fromisoformat(_clean(d.get_text()))
            except ValueError:
                try:
                    run_date = datetime.strptime(_clean(d.get_text()), "%d/%m/%Y").date()
                except ValueError:
                    run_date = None
        h3 = header.find("h3")
        if h3:
            n = re.search(r"#\s*(\d+)", h3.get_text())
            run_number = int(n.group(1)) if n else None
        for card in header.select(".statistics-card"):
            label = card.select_one(".label")
            if label and "finisher" in label.get_text().lower():
                finishers = _int(card.select_one(".value").get_text() if card.select_one(".value") else None)

    slug = None
    canonical = soup.find("link", rel="canonical")
    for href in ([canonical["href"]] if canonical and canonical.get("href") else []) + \
                [a["href"] for a in soup.select("a[href*='/results/']")]:
        sm = re.search(r"(?:https?://[^/]+)?/([^/]+)/results/", href)
        if sm and sm.group(1) not in {"results", "parkrunner"}:
            slug = sm.group(1)
            break

    rows: list[EventRow] = []
    for tr in soup.select("tr.Results-table-row"):
        a = tr.select_one("a[href*='parkrunner']")
        aid = None
        if a:
            m = re.search(r"parkrunner/(\d+)", a["href"])
            aid = int(m.group(1)) if m else None
        gpos = gcount = None
        gcell = tr.select_one(".Results-table-td--gender .detailed")
        if gcell:
            gm = re.search(r"(\d+)\s*/\s*(\d+)", gcell.get_text())
            if gm:
                gpos, gcount = int(gm.group(1)), int(gm.group(2))
        tcell = tr.select_one(".Results-table-td--time .compact")
        t = None
        if tcell and _clean(tcell.get_text()):
            try:
                t = parse_time(_clean(tcell.get_text()))
            except (ParseError, ValueError):
                t = None
        rows.append(EventRow(
            athlete_id=aid,
            name=tr.get("data-name") or (a.get_text() if a else "Unknown"),
            position=_int(tr.get("data-position")),
            gender=(tr.get("data-gender") or None),
            gender_position=gpos,
            gender_count=gcount,
            age_group=(tr.get("data-agegroup") or None),
            age_grade=_pct(tr.get("data-agegrade")),
            time_seconds=t,
            runs=_int(tr.get("data-runs")),
        ))
    if not rows and header is None:
        raise ParseError("No results table found; is this an event results page?")
    if finishers is None:
        finishers = sum(1 for r in rows if r.time_seconds is not None) or None
    return EventResults(event_slug=slug, event_name=event_name, run_date=run_date,
                        run_number=run_number, finishers=finishers, rows=rows)
