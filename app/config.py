"""Settings (from environment variables) and the runner list (from a YAML file)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_ALIASES = {d[:3]: d for d in WEEKDAYS}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    if not v:
        return default
    return v.lower() in {"1", "true", "yes", "on"}


def _day(value: str) -> str:
    v = value.strip().lower()
    v = _DAY_ALIASES.get(v[:3], v) if v else v
    if v not in WEEKDAYS:
        raise ValueError(f"Unknown weekday: {value!r}")
    return v


@dataclass
class Runner:
    name: str
    athlete_id: int          # numeric part of the parkrun ID, e.g. 1234567 for A1234567
    expect: list[str] = field(default_factory=list)   # weekdays they normally run
    colour: str | None = None

    @property
    def parkrun_id(self) -> str:
        return f"A{self.athlete_id}"


@dataclass
class Settings:
    db_path: Path
    runners_file: Path
    base_url: str
    timezone: str
    parkrun_domain: str
    fetcher: str
    request_delay: tuple[float, float]
    poll_days: list[str]
    poll_times: list[str]
    catchup_day: str
    catchup_time: str
    refresh_cooldown: int
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    smtp_starttls: bool
    smtp_ssl: bool
    email_from: str
    email_to: list[str]
    failure_alert_threshold: int
    scheduler_enabled: bool

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_host and self.email_to and self.email_from)


def parse_athlete_id(value) -> int:
    m = re.fullmatch(r"\s*[Aa]?(\d+)\s*", str(value))
    if not m:
        raise ValueError(f"Not a parkrun ID: {value!r}")
    return int(m.group(1))


def _times(value: str) -> list[str]:
    out = []
    for t in value.split(","):
        t = t.strip()
        if not t:
            continue
        if not re.fullmatch(r"\d{1,2}:\d{2}", t):
            raise ValueError(f"Bad time {t!r}; use HH:MM")
        h, m = t.split(":")
        out.append(f"{int(h):02d}:{m}")
    return out


def load_settings() -> Settings:
    delay = _env("REQUEST_DELAY", "3-6")
    lo, _, hi = delay.partition("-")
    lo_f = float(lo)
    hi_f = float(hi) if hi else lo_f
    catch = _env("CATCHUP", "monday 08:30").split()
    smtp_ssl = _bool("SMTP_SSL", False)
    return Settings(
        db_path=Path(_env("DB_PATH", "/data/parkrun.db")),
        runners_file=Path(_env("RUNNERS_FILE", "/config/runners.yaml")),
        base_url=_env("BASE_URL", "http://localhost:8000").rstrip("/"),
        timezone=_env("TZ", "Europe/London"),
        parkrun_domain=_env("PARKRUN_DOMAIN", "www.parkrun.org.uk"),
        fetcher=_env("FETCHER", "http").lower(),
        request_delay=(lo_f, hi_f),
        poll_days=[_day(d) for d in _env("POLL_DAYS", "saturday,sunday").split(",") if d.strip()],
        poll_times=_times(_env("POLL_TIMES", "11:00,12:30,14:00,17:00,21:00")),
        catchup_day=_day(catch[0]) if catch else "monday",
        catchup_time=_times(catch[1])[0] if len(catch) > 1 else "08:30",
        refresh_cooldown=int(_env("REFRESH_COOLDOWN", "300")),
        smtp_host=_env("SMTP_HOST"),
        smtp_port=int(_env("SMTP_PORT", "465" if smtp_ssl else "25")),
        smtp_user=_env("SMTP_USER"),
        smtp_password=_env("SMTP_PASSWORD"),
        smtp_starttls=_bool("SMTP_STARTTLS", False),
        smtp_ssl=smtp_ssl,
        email_from=_env("EMAIL_FROM"),
        email_to=[e.strip() for e in _env("EMAIL_TO").split(",") if e.strip()],
        failure_alert_threshold=int(_env("FAILURE_ALERT_THRESHOLD", "3")),
        scheduler_enabled=_bool("SCHEDULER_ENABLED", True),
    )


def load_runners(path: Path) -> list[Runner]:
    if not path.exists():
        raise FileNotFoundError(
            f"Runner list not found at {path}. Copy config/runners.example.yaml there and edit it."
        )
    data = yaml.safe_load(path.read_text()) or {}
    runners = []
    seen = set()
    for i, item in enumerate(data.get("runners") or []):
        if not isinstance(item, dict) or "name" not in item or "id" not in item:
            raise ValueError(f"Runner #{i + 1} in {path} needs a name and an id")
        aid = parse_athlete_id(item["id"])
        if aid in seen:
            raise ValueError(f"Runner ID A{aid} is listed twice in {path}")
        seen.add(aid)
        expect = item.get("expect") or []
        if isinstance(expect, str):
            expect = [expect]
        runners.append(Runner(
            name=str(item["name"]).strip(),
            athlete_id=aid,
            expect=[_day(d) for d in expect],
            colour=item.get("colour"),
        ))
    if not runners:
        raise ValueError(f"No runners listed in {path}")
    return runners
