import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtemp, mkdir, copyFile, writeFile, readFile, realpath, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { acquireLauncher } from '../launcher-lock.mjs';

test('launcher names cannot escape their two fixed local lock paths', () => {
  assert.throws(() => acquireLauncher({ name: '../private' }), /Unknown launcher/);
});

test('demo cancellation and duplicate starts target only the isolated project', { timeout: 15000 }, async () => {
  const folder = await realpath(await mkdtemp(join(tmpdir(), 'vipmbb-demo-test-')));
  await mkdir(join(folder, 'scripts'));
  await mkdir(join(folder, 'bin'));
  for (const name of ['demo.mjs', 'compose.mjs', 'runtime.mjs', 'launcher-lock.mjs']) {
    await copyFile(new URL('../' + name, import.meta.url), join(folder, 'scripts', name));
  }
  const log = join(folder, 'commands.jsonl');
  await writeFile(join(folder, 'bin', 'docker'), `#!${process.execPath}\nimport('node:fs').then(fs=>{\nconst args=process.argv.slice(2);\nfs.appendFileSync(process.env.FAKE_DOCKER_LOG,JSON.stringify(args)+'\\n');\nif(args.includes('build')) { process.on('SIGTERM',()=>process.exit(0)); setInterval(()=>{},1000); }\n});\n`, { mode: 0o700 });
  const launch = () => spawn(process.execPath, [join(folder, 'scripts', 'demo.mjs')], {
    cwd: folder,
    env: { ...process.env, PATH: join(folder, 'bin') + ':' + process.env.PATH, FAKE_DOCKER_LOG: log },
    stdio: 'pipe',
  });
  const commands = async () => {
    try { return (await readFile(log, 'utf8')).trim().split('\n').map(JSON.parse); }
    catch { return []; }
  };
  const first = launch();
  const firstFinished = once(first, 'exit');
  let second;
  try {
    const deadline = Date.now() + 6000;
    while (!(await commands()).some(args => args.includes('build'))) {
      assert.ok(Date.now() < deadline, 'Demo reached build stage');
      await new Promise(resolve => setTimeout(resolve, 25));
    }
    second = launch();
    const [secondCode] = await once(second, 'exit');
    assert.equal(secondCode, 1);
    assert.equal((await commands()).some(args => args.includes('stop')), false);
    first.kill('SIGINT');
    const [firstCode] = await firstFinished;
    assert.equal(firstCode, 0);
    const recorded = await commands();
    assert.equal(recorded.filter(args => args.includes('stop')).length, 1);
    assert.equal(recorded.some(args => args.includes('up') || args.includes('run')), false);
    for (const args of recorded) {
      assert.equal(args[args.indexOf('-p') + 1], 'vipmbb-demo');
      assert.equal(args[args.indexOf('-f') + 1], 'compose.demo.yaml');
      assert.equal(args[args.indexOf('--env-file') + 1], join(folder, '.local', 'demo.environment'));
    }
    await assert.rejects(readFile(join(folder, '.local', 'demo-launcher.pid')), { code: 'ENOENT' });
    await assert.rejects(readFile(join(folder, '.env')), { code: 'ENOENT' });
  } finally {
    first.kill('SIGKILL'); second?.kill('SIGKILL');
    await rm(folder, { recursive: true, force: true });
  }
});
