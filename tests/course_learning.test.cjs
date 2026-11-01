const test = require('node:test');
const assert = require('node:assert/strict');
const learning = require('../src/wechat_decrypt_tool/resources/course_learning.js');
const first = 'a'.repeat(32), second = 'b'.repeat(32);
const key = id => `course-wiki:${id}`;
class Storage {
  constructor(rows = {}) { this.rows = new Map(Object.entries(rows)); this.writes = 0; }
  get length() { return this.rows.size; }
  key(index) { return [...this.rows.keys()][index] ?? null; }
  getItem(key) { return this.rows.get(key) ?? null; }
  setItem(key, value) { this.writes++; this.rows.set(key, value); }
  removeItem(key) { this.rows.delete(key); }
}
function backup(records) { return {format: learning.FORMAT, records}; }
const state = {read: true, review: false, scroll: 123, answers: {'0': '本地答案🙂 <script>text</script>'}, assessments: {'0': false}};

test('round trip preserves all article versions and leaves unrelated storage alone', () => {
  const source = new Storage({[key(first)]: JSON.stringify(state), [key(second)]: '{}', unrelated: 'keep'});
  const exported = learning.serialize(learning.collect(source));
  const target = new Storage({unrelated: 'keep'});
  const result = learning.apply(learning.plan(learning.parse(exported), target), target);
  assert.equal(result.imported, 2);
  assert.deepEqual(JSON.parse(target.getItem(key(first))), state);
  assert.equal(target.getItem(key(second)), '{}');
  assert.equal(target.getItem('unrelated'), 'keep');
});

test('existing single-article downloads remain importable', () => {
  const value = learning.parse(JSON.stringify({article: first, title: 'Synthetic', learning: state}));
  assert.equal(value.records.length, 1);
  assert.deepEqual(value.records[0].learning, state);
});

test('default keeps local answers and explicit replacement uses exact original version', () => {
  const storage = new Storage({[key(first)]: JSON.stringify({read: false})});
  const value = backup([{article: first, learning: state}, {article: second, learning: {review: true}}]);
  const preview = learning.plan(value, storage);
  assert.equal(preview.conflicts, 1);
  assert.equal(preview.skipped, 1);
  learning.apply(preview, storage);
  assert.deepEqual(JSON.parse(storage.getItem(key(first))), {read: false});
  learning.apply(learning.plan(value, storage, 'replace'), storage);
  assert.deepEqual(JSON.parse(storage.getItem(key(first))), state);
  assert.deepEqual(JSON.parse(storage.getItem(key(second))), {review: true});
  assert.equal(learning.plan(value, storage, 'replace').changes.length, 0);
});

test('invalid records or duplicate IDs reject entire input before any write', () => {
  for (const value of [
    backup([{article: first, learning: state}, {article: first, learning: {}}]),
    backup([{article: '../invalid', learning: {}}]),
    backup([{article: first, learning: {read: 'true'}}]),
    backup([{article: first, learning: {answers: {'0': 123}}}]),
    backup([{article: first, learning: {assessments: {'0': 'yes'}}}]),
    backup([{article: first, learning: {scroll: Infinity}}]),
    backup([{article: first, learning: {answers: JSON.parse('{"__proto__":"x"}')}}]),
    backup([{article: first, learning: {unknown: true}}]),
    {format: 'another-backup', records: []},
  ]) {
    const storage = new Storage();
    assert.throws(() => learning.plan(value, storage));
    assert.equal(storage.writes, 0);
  }
  assert.throws(() => learning.parse('x'.repeat(learning.MAX_BYTES + 1)), /16 MiB/);
});

test('changed local record invalidates a preview without overwriting it', () => {
  const storage = new Storage();
  const preview = learning.plan(backup([{article: first, learning: state}]), storage);
  storage.setItem(key(first), JSON.stringify({read: false}));
  const before = storage.writes;
  assert.throws(() => learning.apply(preview, storage), /已变化/);
  assert.equal(storage.writes, before);
  assert.deepEqual(JSON.parse(storage.getItem(key(first))), {read: false});
});

test('quota failure rolls back all earlier writes, preserving original raw values', () => {
  const original = '{ "read": false }';
  const storage = new Storage({[key(first)]: original, unrelated: 'unchanged'});
  const set = storage.setItem.bind(storage);
  let calls = 0;
  storage.setItem = (key, value) => { if (++calls === 2) throw Error('quota'); set(key, value); };
  const preview = learning.plan(backup([{article: first, learning: state}, {article: second, learning: {read: true}}]), storage, 'replace');
  assert.throws(() => learning.apply(preview, storage), error => error.rollbackIncomplete === false);
  assert.equal(storage.getItem(key(first)), original);
  assert.equal(storage.getItem(key(second)), null);
  assert.equal(storage.getItem('unrelated'), 'unchanged');
});

test('rollback removes only newly inserted records and reports incomplete rollback', () => {
  for (const rollbackFails of [false, true]) {
    const storage = new Storage({unrelated: 'keep'});
    const set = storage.setItem.bind(storage);
    let calls = 0;
    storage.setItem = (key, value) => { if (++calls === 2) throw Error('quota'); set(key, value); };
    if (rollbackFails) storage.removeItem = () => { throw Error('blocked'); };
    assert.throws(() => learning.apply(learning.plan(backup([{article: first, learning: state}, {article: second, learning: {}}]), storage), storage),
      error => error.rollbackIncomplete === rollbackFails);
    assert.equal(storage.getItem(key(second)), null);
    assert.equal(storage.getItem('unrelated'), 'keep');
    if (!rollbackFails) assert.equal(storage.getItem(key(first)), null);
  }
});

test('export refuses concurrent changes instead of silently dropping a record', () => {
  const storage = new Storage({[key(first)]: JSON.stringify(state)});
  const get = storage.getItem.bind(storage);
  let calls = 0;
  storage.getItem = k => { const raw = get(k); if (++calls === 1) storage.rows.set(key(second), '{}'); return raw; };
  assert.throws(() => learning.collect(storage), /其他页面更新/);
});
