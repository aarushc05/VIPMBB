import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter, once } from 'node:events';
import { mkdtemp, mkdir, copyFile, writeFile, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { compose } from '../compose.mjs';
import { ensureModels } from '../runtime.mjs';

test('compose cancellation waits for child close and does not launch pre-aborted commands', async () => {
  const controller = new AbortController();
  const child = new EventEmitter();
  const killed = [];
  child.kill = signal => { killed.push(signal); };
  let settled = false;
  const pending = compose(['build','app'], { signal: controller.signal, spawnProcess: () => child });
  pending.catch(() => { settled = true; });
  controller.abort();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(settled, false);
  assert.deepEqual(killed, ['SIGTERM']);
  child.emit('close', 0);
  await assert.rejects(pending, { name: 'AbortError' });
  let spawned = false;
  await assert.rejects(compose(['up'], { signal: controller.signal, spawnProcess: () => { spawned = true; } }), { name: 'AbortError' });
  assert.equal(spawned, false);
});

test('model downloads receive cancellation and reject without falling back silently', async () => {
  const original = globalThis.fetch;
  const controller = new AbortController();
  let pullStarted;
  const started = new Promise(resolve => { pullStarted = resolve; });
  globalThis.fetch = async (url, options) => {
    if (url.endsWith('/api/tags')) return { ok: true, json: async () => ({ models: [] }) };
    pullStarted();
    return new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true }));
  };
  try {
    const pending = ensureModels({ signal: controller.signal });
    await started;
    controller.abort();
    await assert.rejects(pending, { name: 'AbortError' });
  } finally { globalThis.fetch = original; }
});

async function fixture() {
  const folder = await mkdtemp(join(tmpdir(), 'vipmbb-launcher-test-'));
  await mkdir(join(folder,'scripts'));
  await mkdir(join(folder,'bin'));
  for (const name of ['dev.mjs','compose.mjs','runtime.mjs','docker-config.mjs','launcher-lock.mjs']) {
    await copyFile(new URL('../'+name, import.meta.url), join(folder,'scripts',name));
  }
  const log = join(folder,'commands.jsonl');
  await writeFile(join(folder,'bin','docker'), `#!${process.execPath}\nimport('node:fs').then(fs=>{\nconst args=process.argv.slice(2);\nfs.appendFileSync(process.env.FAKE_DOCKER_LOG,JSON.stringify(args)+'\\n');\nif(args.includes('build')) { process.on('SIGTERM',()=>process.exit(0)); setInterval(()=>{},1000); }\n});\n`, { mode: 0o700 });
  const launch = () => spawn(process.execPath, [join(folder,'scripts','dev.mjs')], {
    cwd:folder, env:{...process.env,PATH:join(folder,'bin')+':'+process.env.PATH,FAKE_DOCKER_LOG:log}, stdio:'pipe',
  });
  const commands = async () => { try { return (await readFile(log,'utf8')).trim().split('\n').map(JSON.parse); } catch { return []; } };
  return {folder,launch,commands};
}

async function waitFor(predicate, timeout=6000) {
  const start=Date.now();
  while (!(await predicate())) {
    if (Date.now()-start>timeout) throw new Error('Timed out waiting for synthetic launcher.');
    await new Promise(resolve=>setTimeout(resolve,25));
  }
}

test('Ctrl+C during build never starts a later service and releases its lock', { timeout:12000 }, async () => {
  const f=await fixture();
  const child=f.launch();
  const finished=once(child,'exit');
  try {
    await waitFor(async ()=>(await f.commands()).some(args=>args.includes('build')));
    child.kill('SIGINT');
    const [code]=await finished;
    assert.equal(code,0);
    const commands=await f.commands();
    assert.equal(commands.filter(args=>args.includes('stop')).length,1);
    assert.equal(commands.some(args=>args.includes('up') || args.includes('run')),false);
    await assert.rejects(readFile(join(f.folder,'.local','launcher.pid')), { code:'ENOENT' });
  } finally { child.kill('SIGKILL'); await rm(f.folder,{recursive:true,force:true}); }
});

test('a second launcher cannot stop services owned by the first', { timeout:12000 }, async () => {
  const f=await fixture();
  const first=f.launch();
  const firstFinished=once(first,'exit');
  let second;
  try {
    await waitFor(async ()=>(await f.commands()).some(args=>args.includes('build')));
    second=f.launch();
    const [code]=await once(second,'exit');
    assert.equal(code,1);
    assert.equal((await f.commands()).some(args=>args.includes('stop')),false);
    first.kill('SIGINT');
    await firstFinished;
  } finally { first.kill('SIGKILL'); second?.kill('SIGKILL'); await rm(f.folder,{recursive:true,force:true}); }
});
