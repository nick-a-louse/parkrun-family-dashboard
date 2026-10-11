# parkrun family dashboard

A small self-hosted web app that shows a family's parkrun results in one place: the latest weekend, personal bests, trends, run counts, streaks and milestones, for 5k parkrun and 2k junior parkrun. It checks parkrun automatically at weekends, emails you when new results land, and runs as a single Docker container.

parkrun has no public API, so the app reads each runner's public results page (`/parkrunner/<id>/all/`) and, for gender position, the event's results page. It does this politely: a few requests per check, a pause of a few seconds between each, and only at weekends.

## What it does

| | |
|---|---|
| Latest weekend | Each runner's most recent result as a finish token: time, overall and gender position (with field sizes), age grade, PB and milestone badges |
| Runners | Per person and distance: PB, best this year, age grade, best positions, best at each event, weekend streak, and progress to the next milestone |
| Times over time | One chart per distance (2k junior / 5k), everyone with results on the same chart; table view; PNG and SVG download |
| All results | Combined feed, newest first; table on desktop, cards on a phone |
| Downloads | Excel, CSV and JSON of every result (filter with `?runner=Name&distance=2k`) |
| Email | One email per check that finds new results, with the results and a link to the dashboard. A warning email if checks keep failing |
| Automatic checks | Saturdays and Sundays at 11:00, 12:30, 14:00, 17:00 and 21:00; stops for the day once everyone expected has a result. Monday-morning catch-up for corrected results |
| Manual check | The **Check for results** button (limited to once every 5 minutes) |
| Gaps | Every check re-reads full histories, so anything missed while the container was down is filled in next time |
| Light and dark | Follows the device, with a toggle |
| Home Assistant | `GET /api/latest` and `GET /api/summary` (JSON) |

Milestones: junior parkrun 11 (half marathon), 21 (marathon), 50 (ultra marathon), 100, 250; 5k parkrun 25, 50, 100, 250, 500, 1000, plus 10 for juniors. Junior and 5k runs are counted separately, as parkrun does.

## Set up on a server

You need Docker and a login to GitHub's container registry (the image is private while the repo is).

1. **Log in to ghcr.io once.** Create a GitHub personal access token (classic) with only the `read:packages` scope, then:
   ```bash
   echo <token> | docker login ghcr.io -u nick-a-louse --password-stdin
   ```
2. **Create folders and config.**
   ```bash
   sudo mkdir -p /opt/appdata/parkrun/{data,config}
   sudo chown 1000:1000 /opt/appdata/parkrun/data
   sudo mkdir -p /srv/parkrun && cd /srv/parkrun   # docker-compose.yml and .env live here
   # copy docker-compose.yml, .env.example and config/runners.example.yaml from this repo, then:
   cp .env.example .env            # fill in BASE_URL and the SMTP settings
   sudo cp config/runners.example.yaml /opt/appdata/parkrun/config/runners.yaml   # add your runners
   ```
3. **Start it.**
   ```bash
   docker compose up -d
   docker compose logs -f parkrun
   ```
   Open `http://<server>:5062`. On first start it loads every runner's history straight away (this takes about a minute because of the pauses between requests).

Or in Portainer: *Stacks → Add stack*, paste `docker-compose.yml`, and add the `.env` values under *Environment variables*.

### Updating

```bash
cd /srv/parkrun && docker compose pull && docker compose up -d
```
The database lives in `/opt/appdata/parkrun/data`, so updates and restarts keep all data.

### Access

Port 5062 on your LAN. For access away from home, use Tailscale to reach the server's Tailscale address on the same port, and set `BASE_URL` to whichever address you want email links to use. The app has no login, so don't expose it to the internet.

## Your own logo and icon

Put image files next to `runners.yaml` (the `/config` folder) and restart the container:

- `logo.png` (or `.svg`/`.webp`): shown in the header as "Our" followed by the logo. The app turns it into a cut-out painted in the page's text colour (so it follows light/dark mode), trims the empty space, and sizes it so the logo's lowercase letters match the height of "Our" and sit on the same baseline. A dark logo on a plain background works best. If it looks too big or small, set `LOGO_SCALE` (e.g. `1.2` or `0.9`) in `.env`. Without a logo the header reads "Our parkruns".
- `icon.png` or `icon.jpg` (or `.svg`/`.ico`): the browser tab icon and home-screen icon. PNG/JPG icons are trimmed to a small margin, made square, and served at 32, 180, 192 and 512 px; thin line art is thickened at 32 px so it stays visible in a browser tab. Without it a built-in icon is used.

The repo doesn't include any third-party logos; supply your own files.

## Grafana monitoring (optional)

The app publishes Prometheus-format metrics at `/metrics` (`up`, last successful check, failed checks in a row, results per runner, next scheduled check). To have VictoriaMetrics scrape it and Grafana alert on it:

1. **Join the monitoring network.** Add to `docker-compose.yml` (use your VictoriaMetrics network's name, from `docker inspect victoriametrics -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}'`):
   ```yaml
   services:
     parkrun:
       networks: [default, monitoring]
   networks:
     monitoring:
       external: true
       name: grafana_grafana_default   # your network's name
   ```
2. **Scrape it.** Add to VictoriaMetrics' `scrape.yaml` under `scrape_configs:`, then reload (`curl http://localhost:8428/-/reload`):
   ```yaml
     - job_name: parkrun
       scrape_interval: 60s
       static_configs:
         - targets: ["parkrun:8000"]
   ```
3. **Create the alerts and dashboard.** With Grafana on the same network:
   ```bash
   docker exec -e GRAFANA_PASSWORD='<admin password>' parkrun python -m app.grafana_setup
   ```
   This creates three alert rules in a "Parkrun" group beside your existing alerts, using your existing contact points: **Parkrun Dashboard Down** (not scrapeable for 5 minutes), **Parkrun Checks Failing** (3+ failed checks in a row) and **Parkrun Results Not Updating** (no successful check for 8 days). It also creates a "Parkrun dashboard" in Grafana. It's safe to run again; it updates in place. Use `--url` if Grafana isn't reachable as `http://grafana:3000`.

## Configuration

**Runners** go in `runners.yaml` (see [config/runners.example.yaml](config/runners.example.yaml)). Restart the container after editing. Removing someone hides them; their results stay in the database.

**Settings** are environment variables (see [.env.example](.env.example)):

| Variable | Default | |
|---|---|---|
| `BASE_URL` | `http://localhost:8000` | Address used in email links |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | | Your SMTP relay. Email is off when `SMTP_HOST` is empty |
| `SMTP_STARTTLS` / `SMTP_SSL` | `false` | Encryption for the relay connection |
| `EMAIL_FROM`, `EMAIL_TO` | | Sender, and comma-separated recipients |
| `POLL_DAYS`, `POLL_TIMES` | `saturday,sunday` / `11:00,12:30,14:00,17:00,21:00` | Automatic checks (local time) |
| `CATCHUP` | `monday 08:30` | Weekly re-check for corrected results |
| `FETCHER` | `http` | `http`, `browser` (use the `:browser` image, which sets it for you) or `manual` |
| `REQUEST_DELAY` | `3-6` | Random pause between requests, seconds |
| `REFRESH_COOLDOWN` | `300` | Seconds between manual checks |
| `FAILURE_ALERT_THRESHOLD` | `3` | Failed checks in a row before a warning email |
| `TZ` | `Europe/London` | |

Use **Send a test email** on the dashboard to check the SMTP settings.

## If parkrun blocks requests

parkrun's firewall refuses some automated requests (for example from cloud servers). From a home connection, plain requests normally work. If checks start failing with "parkrun refused the request":

1. Switch to the headless-browser image: set `image: ghcr.io/nick-a-louse/parkrun-family-dashboard:browser` and `FETCHER=browser`, then `docker compose up -d`.
2. Or import pages by hand: open a runner's results page in your browser, save it (*Save page as… → Web page, HTML only*), and upload it under **Import a saved page**. Event results pages can be imported the same way to fill in gender positions.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest

# try it with made-up data, no parkrun requests
python scripts/demo_data.py /tmp/demo.db
DB_PATH=/tmp/demo.db RUNNERS_FILE=config/runners.demo.yaml SCHEDULER_ENABLED=false \
  uvicorn app.asgi:app --reload
```

Test fixtures in `tests/fixtures` are anonymised copies of real parkrun page structures. Pushing to `main` runs the tests and publishes `:latest` and `:browser` images to ghcr.io.

```
app/
  parser.py     read parkrunner and event results pages
  fetcher.py    polite HTTP / headless-browser / manual fetchers
  sync.py       fetch, upsert, fill gaps, trigger emails
  stats.py      PBs, streaks, milestones, chart series
  scheduler.py  weekend checks and Monday catch-up
  notify.py     email
  exports.py    CSV / JSON / Excel
  main.py       web app and API
```

Not affiliated with parkrun. Results are parkrun's; this only reads public pages for personal use.
