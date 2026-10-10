#!/usr/bin/env node
import { lstatSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { join } from 'node:path';
import { compose } from './compose.mjs';
import { acquireLauncher } from './launcher-lock.mjs';
import { root } from './runtime.mjs';

const offline = process.argv.includes('--offline');
const local = join(root, '.local');
mkdirSync(local, { recursive: true, mode: 0o700 });
// Compose never loads the private .env, even for configuration interpolation.
const environmentFile = join(local, 'demo.environment');
const emptyEnvironment = '# Synthetic demo: no external credentials.\n';
try {
  writeFileSync(environmentFile, emptyEnvironment, { mode: 0o600, flag: 'wx' });
} catch (error) {
  if (error.code !== 'EEXIST') throw error;
  if (!lstatSync(environmentFile).isFile() || readFileSync(environmentFile, 'utf8') !== emptyEnvironment) {
    throw new Error('The existing .local/demo.environment is not a generated demo file. It was left unchanged; inspect it before starting the demo.');
  }
}
const prefix = ['--env-file', environmentFile, '-f', 'compose.demo.yaml', '-p', 'vipmbb-demo'];
const startup = new AbortController();
let activeCompose, logs, stopping = false, shutdown, releaseLauncher;

async function stage(args) {
  startup.signal.throwIfAborted();
  activeCompose = compose([...prefix, ...args], { signal: startup.signal });
  try { await activeCompose; } finally { activeCompose = undefined; }
  startup.signal.throwIfAborted();
}

async function stop(code = 0) {
  if (stopping) return shutdown;
  stopping = true;
  startup.abort();
  logs?.kill('SIGTERM');
  const pending = activeCompose;
  shutdown = (async () => {
    try {
      await pending?.catch(() => {});
      if (releaseLauncher) await compose([...prefix, 'stop', '--timeout', '10'], { signal: AbortSignal.timeout(25000) });
    } catch { /* Docker may have stopped; named data volumes are never removed. */ }
    finally { releaseLauncher?.(); releaseLauncher = undefined; process.exitCode = code; }
  })();
  return shutdown;
}
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => stop());

try {
  releaseLauncher = acquireLauncher({ name: 'demo-launcher' });
  await stage(['version']);
  if (!offline) await stage(['build', 'app']);
  await stage(['up', '-d', '--wait', 'db']);
  await stage(['run', '--rm', '--no-deps', 'migrate']);
  await stage(['run', '--rm', '--no-deps', 'seed']);
  await stage(['up', '-d', '--no-deps', '--wait', 'app']);
  console.log('\nSynthetic demo ready: http://127.0.0.1:8002\nAll players and measurements are fictional. No Kinexon credentials or model downloads are used.\nCtrl+C stops only the demo; its separate database and your edits are retained.\n');
  logs = spawn('docker', ['compose', ...prefix, 'logs', '--follow', '--tail', '10', 'app'], { cwd: root, stdio: 'inherit' });
  logs.once('error', () => stop(1));
  logs.once('exit', code => { if (!stopping) stop(code || 1); });
} catch (error) {
  if (!stopping) console.error(error.message);
  await stop(stopping ? 0 : 1);
}
