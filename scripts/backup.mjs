import { spawnSync } from 'node:child_process';
import { chmodSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { root } from './runtime.mjs';

// pg_dump runs inside the app with its mounted secret. Only the resulting
// filename reaches this process; database credentials never enter command args.
const result = spawnSync('docker', ['compose', 'exec', '-T', 'app', 'python', '-c',
  'import json; from server.app import data; print(json.dumps(data.backup()))'],
{ cwd: root, encoding: 'utf8', maxBuffer: 1024 * 1024 });
try {
  if (result.status !== 0) throw new Error('Backup failed. Start the app with npm run dev, then retry.');
  const { filename } = JSON.parse(result.stdout);
  if (!/^practice-[0-9TZ]+\.dump$/.test(filename)) throw new Error('Unexpected backup filename.');
  const folder = join(root, '.local', 'backups');
  mkdirSync(folder, { recursive: true, mode: 0o700 });
  const destination = join(folder, filename);
  const copy = spawnSync('docker', ['compose', 'cp', `app:/state/backups/${filename}`, destination], { cwd: root, stdio: 'inherit' });
  if (copy.status !== 0) throw new Error('Backup exists in the Docker volume, but export failed. Check available disk space.');
  chmodSync(destination, 0o600);
  console.log(`Consistent PostgreSQL backup saved to ${destination}\nKeep another encrypted copy in an approved location outside this laptop.`);
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
