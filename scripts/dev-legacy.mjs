#!/usr/bin/env node
// Preserved launcher for the previous Dashboard implementation.

import { existsSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const isWindows = process.platform === 'win32';
const npm = isWindows ? 'npm.cmd' : 'npm';
const venvPython = join(root, '.venv', isWindows ? 'Scripts/python.exe' : 'bin/python');
const systemPython = isWindows ? 'python' : 'python3';
const database = join(root, 'Dashboard/public/data/kinexon.local.sqlite3');
const envFile = join(root, '.env');
const npmCache = join(root, '.npm-cache');
const syncOnly = process.argv.includes('--sync-only');
const offline = process.argv.includes('--offline');

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: root,
    env: process.env,
    stdio: 'inherit',
    ...options,
  });
}

function loadEnvironmentFile() {
  if (!existsSync(envFile)) return;
  process.loadEnvFile(envFile);
}

function ensureDashboardDependencies() {
  if (existsSync(join(root, 'Dashboard/node_modules/sql.js/package.json'))) return;
  console.log('\nPreparing dashboard dependencies...');
  const result = run(npm, ['--prefix', 'Dashboard', 'install', '--cache', npmCache]);
  if (result.status !== 0) process.exit(result.status ?? 1);
}

function pythonCanImportRequests(command) {
  return spawnSync(command, ['-c', 'import requests'], {
    cwd: root,
    stdio: 'ignore',
  }).status === 0;
}

function ensurePython() {
  if (existsSync(venvPython) && pythonCanImportRequests(venvPython)) return venvPython;
  if (!existsSync(venvPython)) {
    console.log('\nCreating the local Python environment...');
    const created = run(systemPython, ['-m', 'venv', '.venv']);
    if (created.status !== 0) process.exit(created.status ?? 1);
  }
  console.log('\nPreparing Kinexon dependencies...');
  const installed = run(venvPython, ['-m', 'pip', 'install', '-r', 'requirements.txt']);
  if (installed.status !== 0) process.exit(installed.status ?? 1);
  return venvPython;
}

function refreshDatabase() {
  const required = ['KINEXON_USER', 'KINEXON_PASSWORD', 'KINEXON_API_KEY'];
  const missing = required.filter((key) => !process.env[key]);
  if (missing.length) {
    const message = `Missing ${missing.join(', ')}. Copy .env.example to .env and add the project credentials.`;
    if (syncOnly || !existsSync(database)) {
      console.error(`\n${message}`);
      process.exit(1);
    }
    console.warn(`\n${message}\nStarting with the existing SQLite cache.`);
    return;
  }

  const python = ensurePython();
  console.log('\nRefreshing the Kinexon SQLite cache...');
  const syncArgs = syncOnly ? process.argv.slice(2).filter(arg => arg !== '--sync-only') : [];
  const synced = run(python, ['Test/sync_kinexon_sqlite.py', '--team-id', '3', ...syncArgs]);
  if (synced.status !== 0) {
    if (syncOnly || !existsSync(database)) process.exit(synced.status ?? 1);
    console.warn('\nKinexon refresh failed; starting with the existing SQLite cache.');
  }
}

loadEnvironmentFile();
if (!syncOnly) ensureDashboardDependencies();
if (!offline) refreshDatabase();
if (syncOnly) process.exit(0);

// API secrets belong to the Python sync, not the frontend development server.
const dashboardEnv = { ...process.env };
for (const key of ['KINEXON_USER', 'KINEXON_PASSWORD', 'KINEXON_API_KEY']) delete dashboardEnv[key];

console.log('\nStarting the dashboard at http://localhost:3000 ...\n');
const dashboard = spawn(npm, ['--prefix', 'Dashboard', 'run', 'dev', '--', '--host', '127.0.0.1'], {
  cwd: root,
  env: dashboardEnv,
  stdio: 'inherit',
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => dashboard.kill(signal));
}
dashboard.on('exit', (code) => process.exit(code ?? 0));
