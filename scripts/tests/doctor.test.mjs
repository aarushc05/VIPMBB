import test from 'node:test';
import assert from 'node:assert/strict';
import { inspectSetup, supportedNode } from '../doctor.mjs';

const readySetup = {
  nodeVersion: '22.13.0', platform: 'darwin', arch: 'arm64',
  command: () => true, mode: () => 0o600,
  freeBytes: () => 16 * 1024 ** 3, ready: async () => true,
};

test('doctor accepts supported Node versions and rejects older or invalid versions', () => {
  for (const version of ['22.13.0', 'v22.13.1', '22.20.0', '24.0.0']) assert.equal(supportedNode(version), true);
  for (const version of ['22.12.9', '20.20.0', '22.13', 'not-a-version']) assert.equal(supportedNode(version), false);
});

test('doctor makes only the expected read-only Docker probes', async () => {
  const commands = [];
  const checks = await inspectSetup({ ...readySetup, command: args => { commands.push(args); return true; } });
  assert.equal(checks.every(check => check.level === 'ok'), true);
  assert.deepEqual(commands, [['--version'], ['compose', 'version'], ['info', '--format', '{{.ServerVersion}}']]);
});

test('doctor distinguishes missing prerequisites from optional credentials and a stopped application', async () => {
  const commands = [];
  const checks = await inspectSetup({
    ...readySetup, nodeVersion: '20.0.0', command: args => { commands.push(args); return false; },
    mode: () => null, ready: async () => false,
  });
  assert.equal(checks.filter(check => check.level === 'fail').length, 2);
  assert.deepEqual(commands, [['--version']]);
  assert.ok(checks.some(check => check.level === 'warn' && check.message.includes('No .env')));
  assert.ok(checks.some(check => check.level === 'info' && check.message.includes('normal before')));
});

test('doctor warns about broad secret-file permissions, limited disk, and unsupported automatic model installation', async () => {
  const checks = await inspectSetup({ ...readySetup, platform: 'linux', mode: () => 0o644, freeBytes: () => 1024 });
  assert.equal(checks.filter(check => check.level === 'warn').length, 3);
  assert.equal(checks.some(check => check.level === 'fail'), false);
});

test('doctor distinguishes installed CLI from unavailable Compose and engine', async () => {
  const checks = await inspectSetup({ ...readySetup, command: args => args[0] === '--version', freeBytes: () => null });
  assert.equal(checks.filter(check => check.level === 'fail').length, 2);
  assert.ok(checks.some(check => check.message.includes('Free disk space could not be checked')));
});
