#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { root, python, runtimeEnv, ensureDependencies, startModelService, ensureModels } from './runtime.mjs';

const offline = process.argv.includes('--offline');
const children = [];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  process.exitCode = code;
  for (const child of children) child.kill('SIGTERM');
  setTimeout(() => {
    for (const child of children) if (child.exitCode === null) child.kill('SIGKILL');
    process.exit(code);
  }, 1500).unref();
}
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => stop());
function launch(command, args, env, cwd = root) {
  const child = spawn(command, args, { cwd, env, stdio: 'inherit' });
  children.push(child);
  child.on('error', () => { console.error('A local service could not start.'); stop(1); });
  child.on('exit', code => { if (!stopping) { console.error(`A local service stopped (${code}). Restart with npm run dev.`); stop(code || 1); } });
  return child;
}
async function requireFreePort(port) {
  await new Promise((resolve, reject) => {
    const server = createServer();
    server.once('error', () => reject(new Error(`Port ${port} is already in use. Stop the existing VIP-MBB app or conflicting service first.`)));
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
}
try {
  await requireFreePort(8000);
  await requireFreePort(8001);
  ensureDependencies();
  const env = { ...process.env, PYTHONUNBUFFERED: '1', OLLAMA_URL: 'http://127.0.0.1:11434', ...(offline ? { VIPMBB_DISABLE_AUTO_SYNC: '1' } : {}) };
  launch(python, ['-m', 'uvicorn', 'local_app.app:app', '--host', '127.0.0.1', '--port', '8000', '--no-access-log'], env);
  launch(python, ['-m', 'local_app.worker'], env);
  launch(process.execPath, [root+'/web/node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '8001', '--strictPort'], runtimeEnv, root+'/web');
  console.log('\nVIP-MBB is starting at http://127.0.0.1:8001\nKeep this terminal open. Ctrl+C stops all services started here.\n');
  (async () => {
    const child = await startModelService({ offline, onSpawn: child => children.push(child), cancelled: () => stopping });
    if (stopping) { child?.kill('SIGTERM'); return; }
    if (!offline) await ensureModels();
    console.log('Local model service ready. No cloud AI is used.');
    const indexing = spawn(python, ['-c', 'from local_app import data,knowledge; data.initialize(); knowledge.initialize(); data.enqueue_job("index", {})'], { cwd: root, env, stdio: 'ignore' });
    children.push(indexing);
  })().catch(() => console.warn('Local model setup is incomplete. Reports and bounded queries remain usable. Retry npm run setup:models.'));
} catch (error) {
  console.error(error.message);
  stop(1);
}
