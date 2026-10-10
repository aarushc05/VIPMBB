import { existsSync, mkdirSync, createReadStream, createWriteStream } from 'node:fs';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { pipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const root = dirname(dirname(fileURLToPath(import.meta.url)));
export const ollama = join(root, '.runtime', 'ollama', 'ollama');
export const modelNames = ['qwen3:4b', 'embeddinggemma:latest'];
export const runtimeEnv = { ...process.env, OLLAMA_MODELS: join(root, '.runtime', 'models'), OLLAMA_NO_CLOUD: '1', OLLAMA_HOST: '127.0.0.1:11434', OLLAMA_NUM_PARALLEL: '1', OLLAMA_MAX_LOADED_MODELS: '1', OLLAMA_ORIGINS: 'http://localhost:8000,http://127.0.0.1:8000' };
for (const key of Object.keys(runtimeEnv)) if (key.startsWith('KINEXON_')) delete runtimeEnv[key];

export function run(command, args, options = {}) {
  const result = spawnSync(command, args, { cwd: root, stdio: 'inherit', ...options });
  if (result.status !== 0) throw new Error(`Setup command failed (${result.status ?? 'could not start'}). Check the output above.`);
}

function boundedSignal(signal, milliseconds) {
  return signal ? AbortSignal.any([signal, AbortSignal.timeout(milliseconds)]) : AbortSignal.timeout(milliseconds);
}

export async function modelServiceReady(signal) {
  try { return (await fetch('http://127.0.0.1:11434/api/version', { signal: boundedSignal(signal, 1500) })).ok; }
  catch { return false; }
}

export async function installRuntime({ signal } = {}) {
  signal?.throwIfAborted();
  if (existsSync(ollama)) return;
  if (process.platform !== 'darwin' || process.arch !== 'arm64') throw new Error('Automatic local model setup currently targets Apple Silicon Macs. Install Ollama locally on other platforms.');
  console.log('Downloading the pinned official Ollama runtime (about 160 MB)…');
  const folder = join(root, '.runtime');
  mkdirSync(folder, { recursive: true, mode: 0o700 });
  const archive = join(folder, 'ollama-darwin.tgz');
  const response = await fetch('https://github.com/ollama/ollama/releases/download/v0.40.0/ollama-darwin.tgz', { signal: boundedSignal(signal, 600000) });
  if (!response.ok || !response.body) throw new Error('Could not download the official local model runtime. Retry npm run setup:models.');
  await pipeline(Readable.fromWeb(response.body), createWriteStream(archive, { mode: 0o600 }), { signal });
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(archive)) { signal?.throwIfAborted(); hash.update(chunk); }
  if (hash.digest('hex') !== 'b490b4925a95c5f3dfcd889e566cf3dcd727848d59057fb00b03f1d6630326dc') throw new Error('Runtime checksum mismatch. The download was not installed.');
  mkdirSync(join(folder, 'ollama'), { recursive: true, mode: 0o700 });
  signal?.throwIfAborted();
  run('tar', ['-xzf', archive, '-C', join(folder, 'ollama')]);
}

export async function startModelService({ offline = false, onSpawn = () => {}, cancelled = () => false, signal } = {}) {
  signal?.throwIfAborted();
  if (await modelServiceReady(signal)) {
    console.log('Using the existing loopback Ollama service with the two approved local model names. Its process settings are managed by its owner.');
    return null;
  }
  if (offline && !existsSync(ollama)) throw new Error('No installed model runtime; offline mode will not download one.');
  if (!offline) await installRuntime({ signal });
  signal?.throwIfAborted();
  if (cancelled()) return null;
  const child = spawn(ollama, ['serve'], { cwd: root, env: runtimeEnv, stdio: 'ignore' });
  onSpawn(child);
  child.on('error', () => {});
  const abort = () => child.kill('SIGTERM');
  signal?.addEventListener('abort', abort, { once: true });
  try {
    for (let i = 0; i < 120; i++) {
      signal?.throwIfAborted();
      if (cancelled()) { child.kill('SIGTERM'); return null; }
      if (await modelServiceReady(signal)) { signal?.throwIfAborted(); return child; }
      if (child.exitCode !== null) break;
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    throw new Error('The local model service did not start. Reports and bounded data queries still work without it.');
  } catch (error) {
    child.kill('SIGTERM');
    throw error;
  } finally {
    signal?.removeEventListener('abort', abort);
  }
}

export async function ensureModels({ signal } = {}) {
  signal?.throwIfAborted();
  const response = await fetch('http://127.0.0.1:11434/api/tags', { signal: boundedSignal(signal, 5000) });
  if (!response.ok) throw new Error('Local model service is unavailable.');
  const installed = (await response.json()).models.map(model => model.name);
  for (const name of modelNames) {
    signal?.throwIfAborted();
    if (installed.includes(name)) continue;
    console.log(`Downloading ${name} from the official registry. First setup needs about 3.2 GB and may take several minutes…`);
    const pulled = await fetch('http://127.0.0.1:11434/api/pull', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model: name, stream: false }), signal: boundedSignal(signal, 3600000) });
    const value = await pulled.json();
    if (!pulled.ok || value.status !== 'success') throw new Error(`Model ${name} did not finish downloading. Run npm run setup:models to retry.`);
  }
}
