import { existsSync, mkdirSync, createReadStream, createWriteStream, readFileSync, writeFileSync } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { pipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const root = dirname(dirname(fileURLToPath(import.meta.url)));
export const python = join(root, '.venv', 'bin', 'python');
export const ollama = join(root, '.runtime', 'ollama', 'ollama');
export const modelNames = ['qwen3:4b', 'embeddinggemma:latest'];
export const runtimeEnv = { ...process.env, OLLAMA_MODELS: join(root, '.runtime', 'models'), OLLAMA_NO_CLOUD: '1', OLLAMA_HOST: '127.0.0.1:11434', OLLAMA_NUM_PARALLEL: '1', OLLAMA_MAX_LOADED_MODELS: '1', OLLAMA_ORIGINS: 'http://localhost:8000,http://127.0.0.1:8000' };
for (const key of Object.keys(runtimeEnv)) if (key.startsWith('KINEXON_')) delete runtimeEnv[key];

export function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', ...options });
  if (result.status !== 0) throw new Error(`Setup command failed (${result.status ?? 'could not start'}). Check the output above.`);
}

export function ensureDependencies() {
  if (!existsSync(python)) {
    console.log('Creating the private Python environment…');
    run('python3', ['-m', 'venv', '.venv']);
  }
  const requirementsHash = createHash('sha256').update(readFileSync(join(root, 'requirements.txt'))).digest('hex');
  const stamp = join(root, '.venv', '.vipmbb-requirements');
  if (!existsSync(stamp) || readFileSync(stamp, 'utf8') !== requirementsHash || spawnSync(python, ['-c', 'import requests,fastapi,uvicorn,dotenv,httpx,pytest'], { stdio: 'ignore' }).status !== 0) {
    run(python, ['-m', 'pip', 'install', '-r', 'requirements.txt']);
    writeFileSync(stamp, requirementsHash, { mode: 0o600 });
  }
  const lock = join(root, 'web/package-lock.json');
  const frontendHash = createHash('sha256').update(readFileSync(join(root, 'web/package.json'))).update(existsSync(lock) ? readFileSync(lock) : '').digest('hex');
  const frontendStamp = join(root, 'web/node_modules/.vipmbb-dependencies');
  if (!existsSync(join(root, 'web/node_modules/vite/package.json')) || !existsSync(frontendStamp) || readFileSync(frontendStamp, 'utf8') !== frontendHash) {
    console.log('Installing the interface dependencies…');
    run('npm', ['--prefix', 'web', existsSync(join(root, 'web/package-lock.json')) ? 'ci' : 'install', '--cache', join(root, '.npm-cache')]);
    writeFileSync(frontendStamp, frontendHash, { mode: 0o600 });
  }
}

export async function modelServiceReady() {
  try { return (await fetch('http://127.0.0.1:11434/api/version', { signal: AbortSignal.timeout(1500) })).ok; }
  catch { return false; }
}

export async function installRuntime() {
  if (existsSync(ollama)) return;
  if (process.platform !== 'darwin' || process.arch !== 'arm64') throw new Error('Automatic local model setup currently targets Apple Silicon Macs. Install Ollama locally on other platforms.');
  console.log('Downloading the pinned official Ollama runtime (about 160 MB)…');
  const folder = join(root, '.runtime');
  mkdirSync(folder, { recursive: true, mode: 0o700 });
  const archive = join(folder, 'ollama-darwin.tgz');
  const response = await fetch('https://github.com/ollama/ollama/releases/download/v0.40.0/ollama-darwin.tgz', { signal: AbortSignal.timeout(600000) });
  if (!response.ok || !response.body) throw new Error('Could not download the official local model runtime. Retry npm run setup:models.');
  await pipeline(Readable.fromWeb(response.body), createWriteStream(archive, { mode: 0o600 }));
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(archive)) hash.update(chunk);
  if (hash.digest('hex') !== 'b490b4925a95c5f3dfcd889e566cf3dcd727848d59057fb00b03f1d6630326dc') throw new Error('Runtime checksum mismatch. The download was not installed.');
  mkdirSync(join(folder, 'ollama'), { recursive: true, mode: 0o700 });
  run('tar', ['-xzf', archive, '-C', join(folder, 'ollama')]);
}

export async function startModelService({ offline = false, onSpawn = () => {}, cancelled = () => false } = {}) {
  if (await modelServiceReady()) {
    console.log('Using the existing loopback Ollama service with the two approved local model names. Its process settings are managed by its owner.');
    return null;
  }
  if (offline && !existsSync(ollama)) throw new Error('No installed model runtime; offline mode will not download one.');
  if (!offline) await installRuntime();
  if (cancelled()) return null;
  const child = spawn(ollama, ['serve'], { cwd: root, env: runtimeEnv, stdio: 'ignore' });
  onSpawn(child);
  child.on('error', () => {});
  for (let i = 0; i < 120; i++) {
    if (cancelled()) { child.kill('SIGTERM'); return null; }
    if (await modelServiceReady()) return child;
    if (child.exitCode !== null) break;
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  child.kill('SIGTERM');
  throw new Error('The local model service did not start. Reports and bounded data queries still work without it.');
}

export async function ensureModels() {
  const response = await fetch('http://127.0.0.1:11434/api/tags');
  if (!response.ok) throw new Error('Local model service is unavailable.');
  const installed = (await response.json()).models.map(model => model.name);
  for (const name of modelNames) {
    if (installed.includes(name)) continue;
    console.log(`Downloading ${name} from the official registry. First setup needs about 3.2 GB and may take several minutes…`);
    const pulled = await fetch('http://127.0.0.1:11434/api/pull', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: name, stream: false }), signal: AbortSignal.timeout(3600000) });
    const value = await pulled.json();
    if (!pulled.ok || value.status !== 'success') throw new Error(`Model ${name} did not finish downloading. Run npm run setup:models to retry.`);
  }
}
