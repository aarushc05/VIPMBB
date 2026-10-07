# VIPMBB — previous dashboard documentation

This document preserves the earlier workflow. For the new local practice reports and assistant, use README.md. Paths and commands below describe the old Dashboard and are not the current default.

Read-only tools for exploring the Georgia Tech McCamish Kinexon Sport App API.

## One-time setup

Install Node.js 22.13+ and Python 3.10+ first. Do not share credentials in chat.

```bash
git clone https://github.com/aarushc05/VIPMBB.git
cd VIPMBB
cp .env.example .env
```

Obtain the HTTP Basic Auth credentials and API key from the project
administrator. Put them in the local `.env` file. Keep that file out of source
code, GitHub, screenshots, and chat:

```dotenv
KINEXON_USER=your-username
KINEXON_PASSWORD=your-password
KINEXON_API_KEY=your-api-key
```

After that, the complete local application starts with one command from the
repository root:

```bash
npm run dev
```

The command installs missing dashboard and Python dependencies, refreshes the
SQLite cache incrementally, and starts the dashboard at `http://localhost:3000`.
If Kinexon is temporarily unavailable but a cache already exists, the dashboard
starts with the cached data.

The first run (or an upgrade of the older partial cache) backfills all cached
history and checks session assignments, so it can take several minutes.
The local server is bound to your computer, not your local network.

While the dashboard is running, refresh in another terminal with `npm run sync`,
then click **Reload cache**. To browse without contacting Kinexon, run
`npm run dev:offline`. The sync command fails clearly if credentials are absent;
the dev command may use an existing cache, with its age and coverage displayed.

## Connectivity test

Team `3` is the Georgia Tech men's basketball team:

```bash
python Test/test_kinexon_api.py --team-id 3
```

## Privacy-conscious API inventory

Run the discovery script locally after exporting the credentials from `.env`
into the terminal environment:

```bash
python Test/discover_kinexon_api.py --team-id 3
```

The default scan covers the preceding 730 days and samples up to three recent
sessions. It makes only read-only GET requests and intentionally skips raw
position and inertial exports. The generated report is written to:

```text
reports/kinexon_api_inventory.json
```

The report includes API paths/statuses, metric and event names, record counts,
field names and types, and coarse session/phase summaries. It excludes
credentials, player names, player IDs, session descriptions, and raw performance
values. Reports are ignored by Git and should be reviewed before sharing.

Useful options:

```bash
# Scan four years and inspect five recent sessions
python Test/discover_kinexon_api.py --team-id 3 --days 1460 --sample-sessions 5

# Choose a different local output path
python Test/discover_kinexon_api.py --team-id 3 --output reports/my_inventory.json
```

## SQLite-backed all-player dashboard

Create or refresh the protected SQLite cache for current, former, and assigned-only players:

```bash
npm run sync
```

The initial synchronization requests up to four years of data. Later runs only
refresh a 14-day overlap from the previous sync checkpoint, so historical data
is not downloaded repeatedly. To rebuild or extend a specific period, supply an
explicit start date:

```bash
npm run sync -- --start 2022-09-01
```

The database is written to
`Dashboard/public/data/kinexon.local.sqlite3`. It is ignored by Git and contains
protected roster and performance information. Do not commit or distribute it.

The sync classifies `Game` and `Match` labels as **Game-labelled** activity.
`Practice`, `Training`, and `Shootaround` are practice. Mixed game/practice
labels, testing, and unlabeled sessions appear under **Needs review**. Source
labels appear in session details. Labels do not prove a session is an official
contest, and multiple sessions may belong to one game.

Every selected session's assigned players are collected. Statistics are fetched
for the union of roster and assigned players, not a one-player sample. Missing
measurements remain identifiable; genuine zeros are preserved. Failed or
malformed requests return a nonzero status and do not advance the checkpoint.
A `.bak` SQLite snapshot is kept before each sync. Removed upstream records in
a successful queried range are reconciled; outside-range history is retained.

Run `npm run dev` from the repository root, then open `http://localhost:3000`.
The dashboard automatically opens the local
SQLite cache. It supports arbitrary start and end dates and queries the database
entirely inside the browser. **Last 7 days** and **Last 30 days** always mean the
actual calendar periods ending today. If no activity occurred, the dashboard
shows an empty-state message rather than shifting the range backward. All dates
are Atlanta calendar days; UTC timestamps are converted before grouping.
**View latest recorded 30 days** is an explicit, separate shortcut to older data.
Empty periods beyond the last successful sync are marked unverified.

Choose **All historical participants** to include former players, or restrict
to the active roster at the last sync. Select any of 13 metrics and click a
player to focus the chart. The session log includes sessions without statistics
and shows assignment coverage when available. Exposure is tracked time, not
official playing time. Movement actions are acceleration, deceleration and
change-of-direction event counts, not an inferred intensity classification.

The official schedule cross-check covers the 2025–26 regular season only,
using Georgia Tech Athletics' schedule verified on September 30, 2026. It
compares calendar dates, not session identities. Practice on a scheduled game
day is flagged for investigation, never silently reclassified as game workload.

The private hosted dashboard does not contain the protected database. Use its
**Open SQLite file** button to open the local database in the browser; the file is
not uploaded to an application server.

The SQLite database includes player names and performance data. Keep it local,
do not commit it, and do not distribute it without an approved access-control
and data-governance plan.

Production builds refuse to include `.sqlite`, `.sqlite3`, `.db`, `.local.json`,
or `.env` files in public assets. Publish only from a clean source checkout
without the private cache. This safeguard does not affect `npm run dev`.

The older `extract_dashboard_data.py` JSON command is legacy; use SQLite sync
for the dashboard. Its shared API helpers are still used internally.

## Offline checks

```bash
npm test
.venv/bin/python -m unittest discover -s Test -p test_sync_sqlite.py -v
npm --prefix Dashboard run typecheck
```

No credentials are required for these checks. To additionally reconcile all
dashboard totals with your actual cache locally, set `KINEXON_TEST_DB` to the
absolute SQLite path before running `npm test`. Tests print aggregate counts,
not player names or individual performance records.
