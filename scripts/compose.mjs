import { spawn } from 'node:child_process';
import { root } from './runtime.mjs';

export function compose(args, { env = process.env, signal, spawnProcess = spawn, ...options } = {}) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) { reject(signal.reason); return; }
    const child = spawnProcess('docker', ['compose', ...args], { cwd: root, env, stdio: 'inherit', ...options });
    let failure, forceKill;
    const abort = () => {
      child.kill('SIGTERM');
      forceKill = setTimeout(() => child.kill('SIGKILL'), 5000);
      forceKill.unref?.();
    };
    signal?.addEventListener('abort', abort, { once: true });
    // Wait for close, not just abort/error: startup cleanup must finish before
    // a final compose stop, otherwise a still-running up could restart services.
    child.once('error', () => { failure = new Error('Docker is unavailable. Install and start Docker Desktop, then retry.'); });
    child.once('close', code => {
      clearTimeout(forceKill);
      signal?.removeEventListener('abort', abort);
      if (signal?.aborted) reject(signal.reason);
      else if (failure) reject(failure);
      else if (code === 0) resolve();
      else reject(new Error(`Docker command failed (${code}). See the output above; your database volume is retained.`));
    });
    if (signal?.aborted) abort();
  });
}
