import test from 'node:test';
import assert from 'node:assert/strict';

let sequence = 0;
const freshApi = () => import(`../src/api.js?test=${++sequence}`);
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });

test('read requests do not send credentials from application configuration', async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (...args) => { calls.push(args); return response({ sessions: [] }); };
  try {
    const { api } = await freshApi();
    await api('/sessions');
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], '/api/sessions');
    assert.equal(calls[0][1].credentials, 'same-origin');
    assert.equal(calls[0][1].headers.Authorization, undefined);
    assert.equal(calls[0][1].headers['X-VIPMBB-CSRF'], undefined);
  } finally { globalThis.fetch = original; }
});

test('write requests bootstrap CSRF once and serialize structured payloads', async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (...args) => {
    calls.push(args);
    return response(args[0] === '/api/bootstrap' ? { csrf_token: 'test-local-token' } : { saved: true });
  };
  try {
    const { post } = await freshApi();
    await post('/knowledge', { title: 'Drill', body: 'Local note.' });
    await post('/backup');
    assert.equal(calls.length, 3);
    assert.equal(calls[0][0], '/api/bootstrap');
    assert.equal(calls[1][1].headers['X-VIPMBB-CSRF'], 'test-local-token');
    assert.equal(calls[2][1].headers['X-VIPMBB-CSRF'], 'test-local-token');
    assert.deepEqual(JSON.parse(calls[1][1].body), { title: 'Drill', body: 'Local note.' });
  } finally { globalThis.fetch = original; }
});

test('expired CSRF token is cleared so the next explicit retry gets a fresh token', async () => {
  const original = globalThis.fetch;
  let bootstraps = 0;
  let writes = 0;
  globalThis.fetch = async path => path === '/api/bootstrap'
    ? response({ csrf_token: `token-${++bootstraps}` })
    : ++writes === 1 ? response({ detail: 'Reload and retry.' }, 403) : response({ saved: true });
  try {
    const { post } = await freshApi();
    await assert.rejects(post('/backup'), /Reload and retry/);
    await post('/backup');
    assert.equal(bootstraps, 2);
    assert.equal(writes, 2);
  } finally { globalThis.fetch = original; }
});

test('validation messages reach the UI instead of pretending a write succeeded', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => response({ detail: 'Start date must not be after end date.' }, 400);
  try {
    const { api } = await freshApi();
    await assert.rejects(api('/sessions?start=2026-10-08&end=2026-10-07'), /Start date must not be after end date/);
  } finally { globalThis.fetch = original; }
});

test('network outages have actionable messages and abort signals remain distinguishable', async () => {
  const original = globalThis.fetch;
  try {
    const { api } = await freshApi();
    globalThis.fetch = async () => { throw new TypeError('Failed to fetch'); };
    await assert.rejects(api('/status'), /Cannot reach the local server/);
    globalThis.fetch = async () => { throw new DOMException('Aborted', 'AbortError'); };
    await assert.rejects(api('/status'), error => error.name === 'AbortError');
  } finally { globalThis.fetch = original; }
});

test('missing bootstrap tokens prevent writes', async () => {
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async path => { calls.push(path); return response({}); };
  try {
    const { post } = await freshApi();
    await assert.rejects(post('/backup'), /security token/);
    assert.deepEqual(calls, ['/api/bootstrap']);
  } finally { globalThis.fetch = original; }
});
