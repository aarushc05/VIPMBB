import { readFileSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { root } from './runtime.mjs';

export function acquireLauncher() {
  const path = join(root, '.local', 'launcher.pid');
  try {
    const original = statSync(path);
    const pid = Number(readFileSync(path, 'utf8').trim());
    if (!Number.isSafeInteger(pid) || pid < 1) throw new Error('The launcher lock is invalid. Inspect .local/launcher.pid before retrying.');
    try {
      process.kill(pid, 0);
      throw new Error('A VIP-MBB launcher is already running. Use its terminal or stop it before starting another.');
    } catch (error) {
      if (error.code !== 'ESRCH') throw error;
    }
    if (statSync(path).ino !== original.ino) throw new Error('Another launcher is starting. Retry shortly.');
    unlinkSync(path);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  // Exclusive creation resolves two simultaneous starts without touching services.
  writeFileSync(path, String(process.pid), { flag: 'wx', mode: 0o600 });
  return () => {
    try {
      if (readFileSync(path, 'utf8').trim() === String(process.pid)) unlinkSync(path);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
  };
}
