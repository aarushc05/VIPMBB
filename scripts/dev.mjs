#!/usr/bin/env node
import { existsSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { join } from 'node:path';
import { compose } from './compose.mjs';
import { ensureDockerConfig } from './docker-config.mjs';
import { acquireLauncher } from './launcher-lock.mjs';
import { root, startModelService, ensureModels } from './runtime.mjs';

const offline = process.argv.includes('--offline');
const env = { ...process.env, VIPMBB_DISABLE_AUTO_SYNC: offline ? '1' : '0' };
const startup = new AbortController();
let model, logs, activeCompose, shutdown, releaseLauncher, stopping = false;
async function stage(args) {
  startup.signal.throwIfAborted();
  activeCompose = compose(args, { env, signal: startup.signal });
  try { await activeCompose; } finally { activeCompose = undefined; }
  startup.signal.throwIfAborted();
}
async function stop(code = 0) {
  if (stopping) return shutdown;
  stopping = true;
  startup.abort();
  logs?.kill('SIGTERM');
  model?.kill('SIGTERM');
  const pending = activeCompose;
  shutdown = (async () => {
    try {
      await pending?.catch(() => {});
      // A second launcher must never stop the first launcher's services.
      if (releaseLauncher) await compose(['stop', '--timeout', '10'], { env, signal: AbortSignal.timeout(25000) });
    } catch { /* Docker may already be stopped or unresponsive. */ }
    finally { releaseLauncher?.(); releaseLauncher = undefined; process.exitCode = code; }
  })();
  return shutdown;
}
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => stop());

try {
  ensureDockerConfig();
  releaseLauncher = acquireLauncher();
  await stage(['version']);
  if (!offline) await stage(['build', 'app']);
  await stage(['up', '-d', '--wait', 'db']);
  await stage(['run', '--rm', '--no-deps', 'migrate']);
  if (existsSync(join(root, '.local', 'practice.sqlite3'))) {
    await stage(['run', '--rm', '--no-deps', 'import', 'python', '-m', 'server.app.import_sqlite', '/legacy/practice.sqlite3', '--if-empty']);
  }
  startup.signal.throwIfAborted();
  try {
    model = await startModelService({ offline, cancelled: () => stopping, signal: startup.signal, onSpawn: child => { model = child; } });
    if (!offline) await ensureModels({ signal: startup.signal });
  } catch {
    startup.signal.throwIfAborted();
    console.warn('Local model setup is incomplete. Reports and bounded queries still work; retry npm run setup:models.');
  }
  startup.signal.throwIfAborted();
  await stage(['up', '-d', '--wait', 'app', 'worker']);
  await stage(['exec', '-T', 'app', 'python', '-c', 'from server.app import data; data.enqueue_job("index", {})']);
  console.log('\nVIP-MBB is ready: http://127.0.0.1:8001\nPostgreSQL data stays in its Docker volume. Ctrl+C stops services without deleting data.\n');
  logs = spawn('docker', ['compose', 'logs', '--follow', '--tail', '10', 'app', 'worker'], { cwd: root, env, stdio: 'inherit' });
  logs.once('error', () => stop(1));
  logs.once('exit', code => { if (!stopping) stop(code || 1); });
} catch (error) {
  if (!stopping) console.error(error.message);
  await stop(stopping ? 0 : 1);
}
