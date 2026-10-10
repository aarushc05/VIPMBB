import { spawn } from 'node:child_process';
import { root } from './runtime.mjs';

export function compose(args, { env = process.env, ...options } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn('docker', ['compose', ...args], { cwd: root, env, stdio: 'inherit', ...options });
    child.once('error', () => reject(new Error('Docker is unavailable. Install and start Docker Desktop, then retry.')));
    child.once('exit', code => code === 0 ? resolve() : reject(new Error(`Docker command failed (${code}). See the output above; your database volume is retained.`)));
  });
}
