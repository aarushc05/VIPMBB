import { chmodSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { join } from 'node:path';
import { root } from './runtime.mjs';

export function ensureDockerConfig() {
  const folder = join(root, '.local', 'secrets');
  mkdirSync(folder, { recursive: true, mode: 0o700 });
  chmodSync(folder, 0o700);
  const password = join(folder, 'postgres_password');
  if (!existsSync(password)) {
    writeFileSync(password, randomBytes(32).toString('hex'), { mode: 0o600, flag: 'wx' });
  }
  chmodSync(password, 0o600);
  const env = join(root, '.env');
  if (!existsSync(env)) {
    writeFileSync(env, '# Optional Kinexon credentials. Never commit this file.\nKINEXON_USER=\nKINEXON_PASSWORD=\nKINEXON_API_KEY=\n', { mode: 0o600, flag: 'wx' });
  }
  chmodSync(env, 0o600);
}
