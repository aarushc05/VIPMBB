# VIP-MBB · Practice Intelligence

Local practice reports and a source-backed performance assistant for Georgia Tech men's basketball. PostgreSQL holds the authoritative measurements, assignments, reports, notes, and conversations. Docker runs the application and ingestion worker; Ollama runs natively on your Mac for Apple Silicon acceleration. No cloud AI or hosting account is required.

## Start on your laptop

Tested on an Apple M4 Mac with 16 GB RAM. You need:

- [Docker Desktop](https://www.docker.com/products/docker-desktop/), installed **and running**. Allow roughly 4 GB of Docker memory; leave memory available for native Ollama.
- [Node.js 22.13+](https://nodejs.org/) and [Git](https://git-scm.com/downloads).
- At least 8 GB of free disk space for images, model downloads, and your database, plus room for backups.
- Authorized Kinexon access if you want to download or refresh team data.

```sh
git clone https://github.com/aarushc05/VIPMBB.git VIP-MBB
cd VIP-MBB
cp -n .env.example .env
chmod 600 .env
open -e .env
```

Enter `KINEXON_USER`, `KINEXON_PASSWORD`, and `KINEXON_API_KEY` in that file. Obtain them privately from the authorized team administrator. Do not paste them into chat, screenshots, issues, or Git. Environment-file values are read literally; shell exports take precedence when running the importer outside Docker.

```sh
npm run dev
```

Open **http://127.0.0.1:8001**. Keep the terminal open. First setup builds the images and downloads the local models (about 3.2 GB); subsequent starts reuse cached layers and models. No separate `npm install`, Python environment, PostgreSQL installation, or Ollama installation is needed on the tested Mac.

The launcher:

1. Creates a random PostgreSQL password in the ignored, private `.local/secrets/` directory.
2. Starts PostgreSQL and applies versioned schema migrations.
3. Imports an existing `.local/practice.sqlite3` **only when the new database is empty**, with full-table verification and without modifying the source.
4. Starts native Ollama, the API/interface, and the ingestion worker.

Press **Ctrl+C** or run `npm run stop` to stop the containers. PostgreSQL data remains in its Docker volume. Never use `docker compose down -v` for this application: `-v` deletes the database volume.

### Existing users

Stop the old app before your first Docker start. Preserve `.env`, `.local/`, and `.runtime/`. The old SQLite database stays untouched as a rollback copy; after migration, **PostgreSQL is authoritative** and new work is not written back to SQLite. An old snapshot is never reapplied over populated PostgreSQL data.

If the importer detects an uncheckpointed SQLite WAL, it stops rather than risk losing recent records. Close the old app cleanly and obtain a consistent snapshot; do not delete WAL files to bypass this check.

### Fresh clones and shared data

Git contains code, **not athlete data**. A fresh clone has an empty database unless an approved backup is restored. Recent dates may genuinely have no returned recordings. Import authorized historical dates from System → Import Kinexon data, or:

```sh
npm run sync -- --start 2026-02-01 --end 2026-03-04
```

This queues work for the running worker. Follow progress in System or `npm run logs`, then select matching dates in the report library. Requests are limited to 367 inclusive days; split longer backfills.

This release runs **one database on your laptop**. Another person cloning and starting the project creates a separate database—it does not connect to yours automatically. Shared team access will require an approved reachable host, authentication, user ownership, and HTTPS. Those are deliberately not enabled in this loopback-only release. Never publish the database or place it in the public GitHub repository.

## Everyday commands

| Command | Purpose |
| --- | --- |
| `npm run dev` | Build/update and start the complete local application |
| `npm run dev:offline` | Start already-built images/models without auto-polling or downloads |
| `npm run stop` | Stop containers without deleting data |
| `npm run status` | Check container health |
| `npm run logs` | Follow API and worker logs |
| `npm run sync -- --start YYYY-MM-DD --end YYYY-MM-DD` | Queue a bounded Kinexon import |
| `npm run backup` | Create and export a consistent PostgreSQL backup to `.local/backups/` |
| `npm run setup:models` | Install/repair the native local model runtime and approved models |
| `npm test` | Build and test against a separate synthetic PostgreSQL database |

Offline mode requires a completed first setup. It disables automatic Kinexon polling, not an intentional manual Sync. Your laptop must be awake for scheduled imports and report generation. On restart, the worker catches up and retries uncovered ranges; historical backfill remains explicit.

For updates, back up first, then:

```sh
git pull --ff-only
npm run dev
```

Preserve any local edits if Git reports a conflict; do not reset them. The launcher applies pending database migrations before starting the API.

## Reports and assistant

The report library supports real last-week/last-month ranges, custom dates, all history, activity filters, and search. Empty recent ranges stay empty—historical March records are not relabeled as September activity.

Each report shows all returned assigned-player records, participant coverage, unavailable measurements, workload/exposure, phase detail, classification review, and print/PDF output. Missing values remain unavailable; real source zeros remain zero. A successful sync is not proof of complete measurements.

Practice, game, and unknown recordings remain separate. Source labels are provisional, known home-game dates trigger review, and ambiguous or mixed recordings are not silently declared games. Human review establishes activity type, not competitive boundaries or sensor completeness. Practice baselines require at least three earlier reviewed practices with matching exposure denominators, using up to five and weighting by exposure. Games and unknown recordings do not receive practice baselines.

The assistant translates a question into a bounded intent. **Python/PostgreSQL calculates numerical facts; the model never executes arbitrary SQL.** Definitions and notes use PostgreSQL full-text search plus pgvector similarity, with source citations and lexical fallback. Ambiguous names or unsupported calculations request clarification. Workload is not treated as a diagnosis of injury, fatigue, effort, readiness, or basketball execution.

Current ingestion covers every discovered assigned player for the selected session/phase metrics: exposure, distance, mechanical/acceleration load, metabolic work, peak speed, movement-event counts, and data quality. It does **not** claim to download every Kinexon metric, raw tracking coordinate, video, or shot stream.

## Architecture

| Component | Location / behavior |
| --- | --- |
| React client | `client/`; built once and served by FastAPI |
| Python application | `server/app/`; API, deterministic analytics, retrieval, importer, worker |
| Schema migrations | `server/migrations/`; Alembic, typed dates/timestamps, JSONB, pgvector |
| Docker configuration | `compose.yaml`, `infra/`; health checks, non-root app, private database |
| PostgreSQL data | Docker named volume `vipmbb_postgres_data` |
| Server-side backups | Docker named volume `vipmbb_app_state`, `/state/backups/` |
| Exported backups / private secrets | `.local/`; ignored by Git and Docker builds |
| Local models | `.runtime/`; ignored by Git and Docker builds |
| Synthetic regression tests | `tests/` and `client/tests/` |
| Previous prototype | `Dashboard/`, `Test/`, `LEGACY_README.md`; preserved, not the current runtime |

The app and worker share one image. A one-shot migration service upgrades the schema before either starts. PostgreSQL-backed jobs have atomic deduplication, short transactional claims, renewable leases, bounded recovery, and ownership checks before source writes. A range ledger tracks completed, failed, and interrupted sync ranges instead of assuming that the latest end date proves continuous coverage. No Redis, Celery, Kubernetes, or separate vector service is needed.

Kinexon credentials are mounted **only in the worker**. The API reads a non-secret configuration flag. Ollama stays on `127.0.0.1:11434`; containers use Docker Desktop's explicit host bridge. Remote AI endpoints are rejected. PostgreSQL publishes no host port in the normal deployment; only the app publishes `127.0.0.1:8001`.

The source host and team may be configured for an authorized deployment, but one database represents one source/team. Never point an already-populated database at another team's overlapping IDs. This is not yet a multi-tenant product.

## Backups and recovery

Use `npm run backup` for a consistent custom-format `pg_dump` exported to `.local/backups/`. The System page's backup button creates the same format inside the Docker app-state volume. Store an encrypted off-laptop copy in an approved location: a Docker volume is persistence, **not a backup**.

Restore into a **new database**, never over the running database. The PostgreSQL image includes `pg_restore`; create a separate target, restore with `--no-owner --no-acl --exit-on-error`, and verify counts, reports, and schema version before a deliberate cutover. PostgreSQL's `vector` extension must be installed in the target before restoring a schema-scoped dump. The regression suite tests backup restoration into an isolated synthetic target.

Keep the original SQLite rollback copy until you have independently verified the migration and backup. Returning to that file later will not include subsequent PostgreSQL notes, reviews, chats, or imports.

## Tests and troubleshooting

`npm test` uses `infra/compose.test.yaml`, a separate `vipmbb_test` database, and per-test isolated schemas. Tests refuse a non-test database name. All fixtures are synthetic; no Kinexon request or athlete data is needed. The test database uses temporary storage and can be discarded safely. Do not run tests against production connection settings.

| Symptom | Check |
| --- | --- |
| Docker unavailable | Open Docker Desktop and wait until its engine is running |
| Port 8001 is occupied | Stop the old VIP-MBB launcher or the conflicting application |
| Empty recent dates | Check available historical dates, sync outcome, and report filters; no returned data does not prove no practice occurred |
| Sync unauthorized / denied | Confirm both HTTP Basic credentials and the API key, plus permission for the configured team |
| Worker not running | Inspect `npm run status` and `npm run logs`; queued is not completed |
| Local model unavailable | Run `npm run setup:models`; reports and clearly labeled bounded queries remain available |
| PostgreSQL authentication fails after editing files | Restore the original `.local/secrets/postgres_password`; changing the file does not rotate a database user's password |
| Schema mismatch | Run `npm run dev` so migrations finish before the app starts |
| Disk full | Export approved backups and inspect Docker storage; do not delete the PostgreSQL volume |

The application is private by default, not encrypted by itself. Use FileVault and protect your backups. Other applications running as your macOS user, or users with Docker access, may access local data. Do not expose this single-user deployment through a public tunnel or change its bind address to share it.
