# Contributing to VIP-MBB

This is a local-first basketball analysis application. A useful contribution should make an answer more trustworthy or a coach's next action easier—not just add another number to the screen.

## Run a safe development environment

1. Fork or clone the repository and create a focused branch.
2. Follow the [README](README.md) prerequisites, then run `npm run doctor`.
3. Use `npm run demo` for the fictional-data walkthrough, or `npm run dev` for an authorized private data installation. Real credentials are not required to contribute.
4. Run `npm test` before opening a pull request. It exercises Node launchers, builds/tests the React client, and runs the backend against a separate temporary PostgreSQL database. Docker must be running; no model downloads, Kinexon credentials, or real player data are needed.

For a quick interface-only check, run `npm --prefix client ci`, then `npm --prefix client test` and `npm --prefix client run build`. The Docker application serves a production build; rerun the appropriate launcher after source changes to rebuild it. These quick checks do not replace the backend suite.

The GitHub Actions workflow runs the same full `npm test` command on pull requests and pushes to `main`. A local pass does not mean a remote run has completed—check the Actions result on your pull request.

## Protect team data

- Never commit `.env`, `.local/`, `.runtime/`, PostgreSQL dumps, real player records, raw API responses, or screenshots containing team data. Use fictional fixtures for issues, tests, and examples.
- Do not paste credentials, authenticated URLs, chat histories, or sensitive logs into public issues. Redact locally before sharing.
- Never use a production database for regression tests. Do not delete Docker volumes to solve a setup issue.
- Keep the app bound to loopback. Authentication, authorization, approved hosting, and TLS are prerequisites for any shared deployment.

The public repository contains code and synthetic examples, not permission to access or redistribute Georgia Tech or Kinexon data. Georgia Tech names, colors, and marks do not imply that this project is an official product. Do not add third-party logos or media without permission or an appropriate usage basis. Repository owners must decide the project's software license; do not assume public visibility grants an open-source license.

## Preserve the data contract

- Missing measurements are not zero. Partial player coverage must remain visible.
- A recording date matching a game schedule is evidence for review, not proof that every measurement belongs to a game.
- Time windows use actual calendar dates. Do not fill empty recent ranges with older sessions.
- Numerical answers come from deterministic database queries and calculations; the language model must not invent values or execute arbitrary SQL.
- Preserve provenance and explain aggregation, denominators, and coverage. Do not turn workload metrics into unsupported injury/readiness diagnoses.
- Add an Alembic migration for schema changes. Never modify a migration already applied to user databases; plan backup and rollback implications.

## Keep changes reviewable

Use small commits and include the problem, solution, tests, and limitations in the pull request. For UI changes, show desktop and narrow-screen screenshots using only demo data. Check keyboard access, contrast, reduced-motion preferences, loading states, and genuinely empty data.

Keep Python application logic in `server/app/`, UI code in `client/`, infrastructure in `infra/` or the Compose files, and launch/setup utilities in `scripts/`. Favor explicit functions and existing dependencies over a new framework. New source integrations must be opt-in and retain the one-database/one-source identity guard until a real multi-tenant design exists.

Before submission, inspect `git diff --check`, `git diff --stat`, and `git status --short`. Verify that only intended code, documentation, and synthetic fixtures are included.
