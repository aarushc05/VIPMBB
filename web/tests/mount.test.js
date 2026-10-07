import test from 'node:test';
import assert from 'node:assert/strict';
import { renderApp } from '../src/mount.js';

test('hot updates reuse the original React root even after module reevaluation', async () => {
  const container = {};
  let created = 0;
  const rendered = [];
  const factory = element => {
    assert.equal(element, container);
    created++;
    return { render: content => rendered.push(content) };
  };
  const root = renderApp(container, 'first render', factory);
  const updatedModule = await import('../src/mount.js?hot-update');
  assert.equal(updatedModule.renderApp(container, 'updated render', factory), root);
  assert.equal(created, 1);
  assert.deepEqual(rendered, ['first render', 'updated render']);
});

test('different DOM containers receive independent roots', () => {
  let created = 0;
  const factory = () => { created++; return { render() {} }; };
  renderApp({}, 'first', factory);
  renderApp({}, 'second', factory);
  assert.equal(created, 2);
});

test('a missing app container fails before creating a root', () => {
  let created = false;
  assert.throws(() => renderApp(null, 'content', () => { created = true; }), /root element is missing/);
  assert.equal(created, false);
});
