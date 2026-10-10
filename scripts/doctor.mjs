#!/usr/bin/env node
import { spawnSync } from 'node:child_process';
import { statSync, statfsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { root } from './runtime.mjs';

const minimumNode = [22, 13, 0];

export function supportedNode(version) {
  const parts = version.replace(/^v/, '').split('.').map(Number);
  if (parts.length < 3 || parts.some(value => !Number.isInteger(value))) return false;
  for (let index = 0; index < minimumNode.length; index++) {
    if (parts[index] !== minimumNode[index]) return parts[index] > minimumNode[index];
  }
  return true;
}

function commandWorks(args) {
  // Capture and discard all command output: diagnostics must not print private
  // Docker configuration, environment variables, or raw error responses.
  const result = spawnSync('docker', args, { cwd: root, stdio: 'ignore', timeout: 8000 });
  return !result.error && result.status === 0;
}

async function appReady() {
  try {
    const response = await fetch('http://127.0.0.1:8001/api/health', {
      signal: AbortSignal.timeout(1500), redirect: 'error',
    });
    if (!response.ok) return false;
    const value = await response.json();
    return value.status === 'ok' && value.storage === 'postgresql';
  } catch { return false; }
}

function fileMode(path) {
  try { return statSync(path).mode & 0o777; }
  catch { return null; }
}

function freeSpace() {
  try { const value = statfsSync(root); return value.bavail * value.bsize; }
  catch { return null; }
}

export async function inspectSetup({
  nodeVersion = process.versions.node,
  platform = process.platform,
  arch = process.arch,
  command = commandWorks,
  mode = fileMode,
  freeBytes = freeSpace,
  ready = appReady,
} = {}) {
  const checks = [];
  const add = (level, message) => checks.push({ level, message });
  add(supportedNode(nodeVersion) ? 'ok' : 'fail', supportedNode(nodeVersion)
    ? 'Node.js meets the 22.13+ requirement.' : 'Install Node.js 22.13 or newer.');

  if (!command(['--version'])) {
    add('fail', 'Docker CLI is missing. Install and open Docker Desktop.');
  } else {
    const compose = command(['compose', 'version']);
    add(compose ? 'ok' : 'fail', compose
      ? 'Docker Compose is installed.' : 'Docker Compose is unavailable. Update Docker Desktop.');
    const running = command(['info', '--format', '{{.ServerVersion}}']);
    add(running ? 'ok' : 'fail', running
      ? 'Docker engine is reachable.' : 'Docker engine is not reachable. Start Docker Desktop or check your Docker context and permissions.');
  }

  add(platform === 'darwin' && arch === 'arm64' ? 'ok' : 'warn', platform === 'darwin' && arch === 'arm64'
    ? 'Apple Silicon: automatic local model setup is supported.'
    : 'Automatic model installation targets Apple Silicon. On other platforms, configure local Ollama separately; reports do not require a model.');

  const envMode = mode(join(root, '.env'));
  if (envMode === null) add('warn', 'No .env file yet. The launcher can create one; live imports require authorized Kinexon credentials.');
  else if (platform !== 'win32' && (envMode & 0o077)) add('warn', '.env is readable by other local users. Run chmod 600 .env.');
  else if (platform === 'win32') add('info', '.env exists. Check its Windows access permissions; credential contents were not read or validated.');
  else add('ok', '.env exists with private file permissions. Credential contents were not read or validated.');

  const bytes = freeBytes();
  if (bytes === null) add('warn', 'Free disk space could not be checked. Allow at least 8 GB for initial setup, plus data and backups.');
  else add(bytes >= 8 * 1024 ** 3 ? 'ok' : 'warn', bytes >= 8 * 1024 ** 3
    ? 'At least 8 GB is free on the project filesystem (Docker storage may be separate).'
    : 'Less than 8 GB is free on the project filesystem. Check space before image/model downloads.');

  const running = await ready();
  add(running ? 'ok' : 'info', running
    ? 'Application and PostgreSQL are healthy at http://127.0.0.1:8001.'
    : 'Application is not currently healthy at http://127.0.0.1:8001. This is normal before npm run dev.');
  return checks;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  console.log('VIP-MBB setup check · read-only · no credential values are read or printed\n');
  const checks = await inspectSetup();
  for (const check of checks) console.log(`[${check.level.toUpperCase()}] ${check.message}`);
  console.log('\nThis check does not start containers, download models, call Kinexon, or alter files.');
  process.exitCode = checks.some(check => check.level === 'fail') ? 1 : 0;
}
