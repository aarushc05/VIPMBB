import { spawnSync } from 'node:child_process';
import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import { compose } from './compose.mjs';
import { root } from './runtime.mjs';

const testFolder = join(root, 'scripts', 'tests');
const launcherTests = readdirSync(testFolder).filter(name => name.endsWith('.test.mjs'));
const launcher = spawnSync(process.execPath, ['--test', ...launcherTests.map(name => join(testFolder, name))], { stdio: 'inherit' });
if (launcher.status !== 0) process.exit(launcher.status || 1);

try {
  await compose(['-f', 'infra/compose.test.yaml', 'build', 'tests']);
  await compose(['-f', 'infra/compose.test.yaml', 'run', '--rm', 'tests']);
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
} finally {
  await compose(['-f', 'infra/compose.test.yaml', 'down']).catch(() => {});
}
