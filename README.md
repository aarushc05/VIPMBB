# VIP-MBB · Practice Intelligence

A private, laptop-local workspace for Georgia Tech men’s basketball practice reports and source-linked questions. Designed for an Apple Silicon Mac with 16 GB RAM. Nothing here publishes athlete data, sends it to a cloud model, or changes Kinexon.

## First-time setup for a teammate

### 1. Check your computer

The supported, tested setup is an **Apple Silicon Mac** (M-series; tested on an M4 with 16 GB RAM). Install [Git for macOS](https://git-scm.com/download/mac), [Node.js **22.13 or newer**](https://nodejs.org/en/download) (including npm), and [Python **3.10 or newer**](https://www.python.org/downloads/macos/) (3.13 tested). Use the macOS installers appropriate for Apple Silicon. Allow at least **6 GB of free storage**, plus room for downloaded data and backups. Open Terminal and confirm the commands are available:

```bash
git --version
node --version
npm --version
python3 --version
```

If a command is missing or its version is too old, install/update that tool before continuing. Native Windows is not supported by the current launcher. Intel Mac and Linux have not been validated; automatic model installation only supports Apple Silicon macOS.

No Docker, paid AI API, cloud database, hosting account, or separate Ollama installation is needed. Internet is needed for the first setup and Kinexon synchronization, not for reading stored reports or running installed local models.

### 2. Clone the project

Run this from the directory where you keep projects:

```bash
git clone https://github.com/aarushc05/VIPMBB.git VIP-MBB
cd VIP-MBB
```

Run all following project commands from this **top-level `VIP-MBB` folder**, not from `Dashboard/`, `Test/`, or `web/`. If you already have a checkout, use that folder instead of cloning over it.

### 3. Configure Kinexon access privately

Obtain authorized credentials from the project administrator through an approved private channel. GitHub access does **not** grant access to the team's Kinexon data. The app is configured for the Georgia Tech McCamish instance and team ID **3**; its API requires both HTTP Basic authentication and an API key.

Create the local configuration file and open it in TextEdit:

```bash
cp -n .env.example .env
chmod 600 .env
open -e .env
```

`cp -n` preserves an existing `.env`. Replace all three placeholders with your project-issued values, save the file, and close the editor:

```dotenv
KINEXON_USER=your-username
KINEXON_PASSWORD=your-password
KINEXON_API_KEY=your-api-key
```

Do not paste secrets into chat, source code, screenshots, or GitHub. `.env` is ignored by Git. The backend reads it directly; the browser and model never receive it. Credentials in `.env` can be added or corrected without restarting the app. Previously exported `KINEXON_*` environment variables take precedence over `.env`; remove stale exports and restart if needed. Without credentials, stored reports, notes, and local chat still work, but live synchronization is unavailable. Rotate any key previously exposed in chat or logs with the project administrator.

### 4. Start the complete application

```bash
npm run dev
```

You do **not** need to run `npm install`, activate a Python environment, or start backend/model services separately. The launcher creates `.venv`, installs pinned Python and frontend dependencies, and starts the private API, background worker, interface, and native local Ollama runtime.

Open **http://127.0.0.1:8001**. Keep the terminal open; **Ctrl+C** stops the application and the services it started. Ports 8000 and 8001 must be free.

First-time model setup downloads approximately **3.2 GB of model weights**, plus the runtime and other dependencies, and can take several minutes. Reports remain usable while models download. Later launches reuse the installed files. Model inference runs locally, one request at a time, with a 4,096-token context to limit memory use. Close memory-heavy apps if necessary.

In **System & data**, check that:

- **Background worker** is running.
- **Credentials configured** is shown after saving `.env`. This checks that values exist, not that they are valid; a successful sync job confirms access.
- **Local assistant** shows the model connected once setup finishes. Bounded fallback mode remains available while the model is unavailable.

### 5. Import data and open your first report

**A fresh clone contains source code, not anyone else's athlete database, notes, or conversations.** Each teammate downloads authorized data into their own private SQLite database. Cloning or pulling this repository does not share local data between laptops.

The first automatic sync checks only the real previous **14 calendar days**. If Kinexon has no recordings in that period, an empty dashboard is expected. **All history** displays what has already been imported; it does not download the entire history.

To import an older period, open **System & data**, enter a start and end date, and choose **Sync data**. Alternatively, after the app has started, open a second terminal in the project folder and run this example historical range:

```bash
npm run sync -- --start 2026-02-01 --end 2026-03-04
```

Wait until its job is **completed**, then open **Practice reports**, choose **All history** or a **Custom** range covering those dates, and open a session. Use **Ask about this session** to try the assistant. Import at most one year per request; split longer periods into separate requests.

A completed import can still have missing upstream measurements. Review report coverage and activity type before interpreting comparisons. **Last 7 days** and **Last 30 days** always refer to actual recent dates, never an older active week.

## Starting again and updating

On later visits, open Terminal in your existing project folder and run `npm run dev`. Your database, notes, reviews, and downloaded models are retained locally.

To get a newer version, stop the app with Ctrl+C, check for local code changes with `git status`, then run:

```bash
git pull --ff-only
npm run dev
```

The launcher installs changed dependencies when needed. If Git reports conflicting local work or divergent branches, preserve that work and ask for help; do not reset or delete it. Use **Create database backup** before significant updates.

## Daily workflow

1. Open **System & data** and confirm the connection, worker, and model status.
2. The worker checks the previous 14 calendar days every 15 minutes while running, catching late uploads and revised recordings. Successful imports queue deterministic report snapshots automatically. It catches up after sleep/restart; no work runs while the laptop is asleep or the app is stopped.
3. Use **Practice reports** to choose a recording. **Last 7 days** and **Last 30 days** are real calendar windows ending today in America/New_York. Empty periods stay empty. **All history** and **Custom** explicitly open older data.
4. Check coverage and **Review activity type** only when you know what the recording contains. Add the reason. A review confirms practice/game/unknown, not sensor accuracy or complete participation. It is retained in the audit history.
5. Read all-player exposure, mechanical load/intensity, distance, acceleration load, movement events, peak speed, and available phase metrics. Use **Print / save PDF** for the complete report, including definitions and caveats.
6. Use **Ask about this session** for a focused conversation, or the general assistant for date-range and multi-practice questions. Source buttons open the underlying report or note.
7. Add drill context and observations in **Knowledge library**. Notes are local and automatically queued for semantic indexing when the embedding model is installed.

Example questions:

- “Who had the highest load per minute in this session?”
- “Show distance over the last five practices.”
- “Compare [full player name] and [full player name] from 2026-02-01 to 2026-03-04.”
- “Show [full player name]’s jump count.”
- “What does mechanical load mean?”
- “Find coaching notes about transition drills.”

The model translates language into a restricted query plan. Python/SQLite calculates numerical results; the model cannot run arbitrary SQL. Notes and definitions use local full-text plus semantic retrieval with source citations. Unsupported calculations or ambiguous names request clarification rather than guessing. No injury, fatigue, effort, readiness, or basketball-execution claims are inferred from tracking load. Local model failure falls back to clearly labeled bounded data tools.

## Data meaning and limitations

- Source `Training`, `Match`, or `Game` labels are **not verified activity identities**. Valid off-game-date Training records can be provisional practice candidates. Known 2025–26 home-game dates and questionable recording boundaries require review; the schedule is evidence, not a classifier or competitive interval detector. The schedule safeguard is season-specific and does not cover every season.
- Imports query the union of the roster and all discovered assignments, including historical players. A successful API request can still return no player record. Reports expose expected versus recorded participation and never claim the missing player was inactive.
- Missing measurements remain unavailable; genuine source zeros remain zero. Old imported cache zeros had ambiguous provenance and are treated as unavailable. Legacy rows are clearly marked and excluded from baselines. Refresh an old range to replace them with current API responses.
- Exposure is tracked time, **not official playing minutes**. Load/minute uses positive exposure, and incompatible time bases are not combined. Whole-recording totals can include warmup/breaks. Peak speed uses a maximum, not a sum.
- Personal baselines need at least three earlier **reviewed** practices with comparable, known exposure denominators. Up to five prior records are used; current/future sessions are excluded. Similar drill mix still matters.
- Phases are available only where upstream metadata and player-phase statistics exist. Overlapping phases cannot be summed; whole-session totals are never prorated into invented drill statistics.
- Mechanical and acceleration load use vendor units. Metabolic work units/calculation remain unverified and are labeled accordingly. These are not effort, efficiency, or health scores.
- Latest recorded activity and latest successful sync are distinct. A recent successful empty sync does not prove that no practice happened; it only shows what this Kinexon instance returned.

## Backfills, offline use, and model repair

Choose start/end dates in **System & data**, or queue a bounded backfill:

```bash
npm run sync -- --start 2026-02-01 --end 2026-03-04
```

Import at most one year per request; split longer history into ranges. Jobs persist in SQLite, failed jobs retain available data, and interrupted jobs get bounded recovery retries. The command shares the worker lock and queues work if the application is already running. Fresh installations start with recent data, not an automatic multi-year download.

```bash
npm run dev:offline    # No automatic Kinexon polling or model downloads
npm run setup:models  # Retry/install the two local models
npm test              # Synthetic backend + frontend tests, then production build
npm run build         # Build only the interface
```

Offline startup can still need dependencies on a brand-new checkout; perform the first setup online. Manual Sync is a deliberate live action even in offline-start mode. The managed model runtime binds loopback only and disables cloud features. If you already have Ollama running on port 11434, the launcher reuses it and leaves its settings/lifecycle under your control; only `qwen3:4b` and `embeddinggemma:latest` are requested by the app.

## Troubleshooting setup

| What you see | What to check |
| --- | --- |
| `npm` / `python3` / `git` not found | Install the prerequisite, reopen Terminal, and check its version before starting the app. |
| Port 8000 or 8001 is already in use | An app instance may already be running. Use it, or stop its terminal with Ctrl+C before starting another. Do not stop unrelated services blindly. |
| Dashboard is empty | Check the sync job outcome and its date range. A fresh clone has no database; import historical dates explicitly, then select matching dates in the report library. |
| Kinexon returns 401 or 403 | Check all three locally saved values and ask the administrator to confirm this account/key can access team 3. Do not share raw authenticated request URLs or secrets in bug reports. |
| Model setup is incomplete / fallback mode | Keep the app running and try `npm run setup:models` in a second terminal. Check internet access and free disk space. Reports and supported bounded queries still work without AI. |
| Security token expired after a restart | Reload the browser page, then retry the action. |
| New practices do not appear automatically | Keep the app running and laptop awake/online. Check worker status and completed/failed jobs; polling runs every 15 minutes, not while the laptop is shut down. |

For a code/setup check, run `npm test` from the project root. It uses synthetic data, does not require live Kinexon credentials, and checks the backend, frontend, and production build. Its dependency setup can require internet on a new checkout.

## Where everything lives

| Path | Purpose |
| --- | --- |
| `web/` | React interface; served on loopback port 8001 in development |
| `local_app/` | Python API, safe analytics/chat, retrieval, sync, worker |
| `.local/practice.sqlite3` | Private SQLite database: raw measurements, assignments, reports, notes, chat, jobs |
| `.local/backups/` | Consistent snapshots created by **Create database backup** |
| `.runtime/` | Downloaded native model runtime and model weights |
| `.env` | Kinexon secrets, server-side only |
| `Dashboard/`, `Test/` | Preserved earlier project; not the default app |

The API binds **127.0.0.1:8000**. It serves only the built interface and explicit API endpoints—not the repository, credentials, or database. Host/Origin checks and write tokens protect against other websites accessing local data. This is a **single-user laptop app**, not a multi-user hosted security design. The database is not encrypted; use FileVault and protect laptop/backups. Other programs running as your macOS user can access your files.

To restore: stop the app, preserve the current `.local` directory as a recovery copy, and restore an approved backup as `.local/practice.sqlite3` into a fresh `.local` directory. Never mix a restored database with old `-wal`/`-shm` files. Do not replace a live database. Restart and inspect dates/jobs before syncing.

The previous Dashboard and its private cache are preserved. The new app reads that cache once for migration but never serves it as a public asset. **Do not publish the old cache or either database.** See `LEGACY_README.md` only if deliberately using the previous workflow.

Nothing is pushed to GitHub automatically. Before a future commit, inspect `git status`, verify private paths are ignored, and review source/docs for accidental secrets or athlete data.
