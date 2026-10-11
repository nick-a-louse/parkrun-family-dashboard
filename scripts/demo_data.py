"""Fill a database with made-up results, for trying the dashboard without touching parkrun.

    python scripts/demo_data.py /tmp/demo.db
    DB_PATH=/tmp/demo.db RUNNERS_FILE=config/runners.demo.yaml SCHEDULER_ENABLED=false \
        uvicorn app.asgi:app --reload
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.db import Database, now_iso  # noqa: E402

random.seed(7)
db = Database(Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/demo.db"))
last_sunday = date.today() - timedelta(days=(date.today().weekday() + 1) % 7)
runners = [
    (1000003, "Sam", "5k", "riverside", "Riverside", 1720, -12, "Male", "VM40-44", 0.9),
    (1000001, "Ava", "2k", "riverside-juniors", "Riverside juniors", 830, -9, "Female", "JW10", 0.85),
    (1000002, "Max", "2k", "riverside-juniors", "Riverside juniors", 790, -11, "Male", "JM10", 0.9),
]
with db.tx() as c:
    for aid, name, dist, slug, ev, start, trend, gender, ag, attend in runners:
        c.execute("INSERT OR REPLACE INTO runners(athlete_id, name, total_5k, total_junior, gender, age_group) "
                  "VALUES(?,?,?,?,?,?)", (aid, name, 0, 0, gender, ag))
        t = start
        n = 0
        for w in range(14, -1, -1):
            day = last_sunday - timedelta(weeks=w) - (timedelta(days=1) if dist == "5k" else timedelta(0))
            if random.random() > attend:
                continue
            n += 1
            t = max(400, t + trend + random.randint(-25, 25))
            num = 560 + (14 - w) if dist == "2k" else 615 + (14 - w)
            fin = random.randint(95, 130) if dist == "2k" else random.randint(380, 470)
            pos = max(1, int(fin * (t - start * 0.6) / (start * 0.9)))
            gcount = fin // 2 + random.randint(-8, 8)
            c.execute(
                "INSERT OR REPLACE INTO results(athlete_id, event_slug, run_number, event_name, distance, run_date, "
                "position, time_seconds, age_grade, parkrun_pb, gender, gender_position, gender_count, finishers, "
                "age_group, event_checked_at, first_seen_at, notified) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (aid, slug, num, ev, dist, day.isoformat(), pos, t, round(100 * start * 0.45 / t, 2), 0,
                 gender, max(1, pos // 2), gcount, fin, ag, now_iso(), now_iso()))
        col = "total_5k" if dist == "5k" else "total_junior"
        c.execute(f"UPDATE runners SET {col}=? WHERE athlete_id=?", (n, aid))
db.set_state("last_success", now_iso())
print("Demo data written to", db.path)
