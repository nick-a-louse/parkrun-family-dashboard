"""Weekend polling and the Monday catch-up."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import Settings
from .sync import Syncer

log = logging.getLogger(__name__)
DOW = {"monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu",
       "friday": "fri", "saturday": "sat", "sunday": "sun"}


def poll(syncer: Syncer, tz: ZoneInfo):
    today = datetime.now(tz).date()
    if syncer.all_expected_in(today):
        log.info("Skipping scheduled check: everyone expected today already has a result")
        return
    out = syncer.sync("schedule")
    log.info("Scheduled check: %s", out.message)


def catchup(syncer: Syncer):
    out = syncer.sync("catchup", catchup=True)
    log.info("Catch-up: %s", out.message)


def start_scheduler(settings: Settings, syncer: Syncer) -> BackgroundScheduler:
    tz = ZoneInfo(settings.timezone)
    sched = BackgroundScheduler(timezone=tz, job_defaults={"coalesce": True, "misfire_grace_time": 1800})
    days = ",".join(DOW[d] for d in settings.poll_days)
    for t in settings.poll_times:
        h, m = t.split(":")
        sched.add_job(poll, CronTrigger(day_of_week=days, hour=int(h), minute=int(m), timezone=tz),
                      args=[syncer, tz], id=f"poll-{t}", max_instances=1)
    h, m = settings.catchup_time.split(":")
    sched.add_job(catchup, CronTrigger(day_of_week=DOW[settings.catchup_day], hour=int(h), minute=int(m),
                                       timezone=tz), args=[syncer], id="catchup", max_instances=1)
    sched.start()
    return sched


def describe(sched: BackgroundScheduler | None) -> list[dict]:
    if sched is None:
        return []
    jobs = sorted(sched.get_jobs(), key=lambda j: j.next_run_time or datetime.max.replace(tzinfo=ZoneInfo("UTC")))
    return [{"id": j.id, "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
             "next_text": f"{j.next_run_time:%a %-d %b} at {j.next_run_time:%H:%M}" if j.next_run_time else ""}
            for j in jobs]
