import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = fs.readFileSync(new URL('../../src/wechat_decrypt_tool/resources/course_library.js', import.meta.url), 'utf8');

function reader(hash, response) {
  class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.open = false; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute() {}
    addEventListener() {}
    scrollIntoView() { this.scrolled = true; }
  }
  const main = new Element('main'), outline = new Element('aside'), skip = new Element('a');
  const descendants = (node) => [node, ...node.children.flatMap(descendants)];
  const document = {
    querySelector: (selector) => selector === 'main' ? main : selector === '#outline' ? outline : skip,
    createElement: (tag) => new Element(tag),
    getElementById: (id) => descendants(main).find((node) => node.id === id),
  };
  const context = vm.createContext({
    document, location: { hash }, window: { addEventListener() {}, scrollTo() {} },
    clearTimeout, setTimeout,
    fetch: async () => ({ ok: true, json: async () => response }),
  });
  // Tests drive the actual route explicitly so asynchronous rendering is awaited.
  vm.runInContext(source.replace('\nroute();\n', '\n'), context);
  return { context, main, document, descendants };
}

const lesson = {
  title: 'Synthetic source',
  sections: [{ id: 'section-0', title: 'Section', summary: 'Summary', start: 0, end: 90, concepts: [], context: [1] }],
  evidence: {
    '1': { id: 1, kind: 'speech', text: 'Synthetic retained speech.', start: 10, end: 20 },
    '-2': { id: -2, kind: 'frame', text: 'Synthetic frame.', start: 30, end: 30, image_sha256: 'a'.repeat(64) },
  },
};

for (const [reference, tag] of [['1', 'p'], ['-2', 'figure']]) {
  test(`source result route opens and scrolls to original ${reference}`, async () => {
    const ui = reader(`#lesson=course/section-0/${reference}`, lesson);
    await ui.context.route();
    const target = ui.document.getElementById(`section-0-evidence-${reference}`);
    assert.equal(target.tagName, tag);
    assert.equal(target.scrolled, true);
    const details = ui.descendants(ui.main).filter((node) => node.tagName === 'details');
    assert.equal(details.filter((node) => node.open).length, 1);
    assert.ok(details.find((node) => node.open).children.includes(target));
  });
}

test('search result carries source destination and renders excerpt as text', async () => {
  const excerpt = '<img src=x onerror=alert(1)> synthetic source text';
  const ui = reader('#search=synthetic', { total: 1, items: [{
    video: 'course', section: 'section-0', title: 'Source', heading: 'Section', start: 0, summary: 'Summary',
    match_source: { id: -2, kind: 'frame', start: 30, end: 30, excerpt },
  }], next_offset: null });
  await ui.context.route();
  const nodes = ui.descendants(ui.main);
  assert.ok(nodes.some((node) => node.href === '#lesson=course/section-0/-2'));
  assert.ok(nodes.some((node) => node.textContent === excerpt));
  assert.equal(nodes.filter((node) => node.tagName === 'img').length, 0);
});
