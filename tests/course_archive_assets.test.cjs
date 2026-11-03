const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const bundle = fs.readFileSync(path.join(__dirname, '../src/wechat_decrypt_tool/resources/course_library.js'), 'utf8');
const start = bundle.indexOf('async function coursewareDocument(');
const end = bundle.indexOf('\nasync function route()', start);
assert.ok(start >= 0 && end > start);

class Element {
  constructor(tag, text = '') { this.tag = tag; this.textContent = text; this.children = []; this.listeners = {}; this.style = {}; }
  append(...children) { this.children.push(...children); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
}

async function render(assets) {
  const main = new Element('main');
  const calls = [];
  const context = vm.createContext({
    main, routeVersion: 7, document: {createElement: tag => new Element(tag)},
    el: (tag, text = '') => new Element(tag, text), heading: () => {},
    notice: text => new Element('p', text),
    link: (text, href) => Object.assign(new Element('a', text), {href}),
    encodeURIComponent,
    api: async url => { calls.push(url); return {path: 'synthetic.pdf', locator: 'synthetic page', source_key: 'visual page 1', extractor: 'synthetic', text: '<script>literal fixture</script>', revision_chain_verified: true, assets, next_offset: null}; },
  });
  vm.runInContext(bundle.slice(start, end), context);
  await context.coursewareDocument('9/' + 'a'.repeat(64) + '/' + 'b'.repeat(64), 7);
  const flatten = node => [node, ...node.children.flatMap(flatten)];
  return {main, elements: flatten(main), calls};
}

test('courseware page uses pinned image and native PDF URLs with useful alt text', async () => {
  const visual = '/wiki/api/courseware/document/9/visual?revision=pinned';
  const source = '/wiki/api/courseware/document/9/source?revision=pinned';
  const {elements, calls} = await render({visual, source, source_is_pdf: true, page: 12});
  assert.match(calls[0], /revision=a{64}&text_sha256=b{64}$/);
  const image = elements.find(x => x.tag === 'img');
  assert.equal(image.src, visual);
  assert.equal(image.alt, '课件第 12 页原图');
  assert.equal(image.loading, 'lazy');
  const original = elements.find(x => x.href === source);
  assert.equal(original.textContent, '打开 PDF 原文件');
  assert.equal(original.target, '_blank');
  assert.equal(original.rel, 'noopener');
  assert.equal(elements.find(x => x.tag === 'pre').textContent, '<script>literal fixture</script>');
});

test('an unavailable preview reports a readable error without hiding extracted text', async () => {
  const {elements} = await render({visual: '/pinned-visual', source: null});
  const image = elements.find(x => x.tag === 'img');
  image.listeners.error();
  assert.equal(image.hidden, true);
  assert.match(elements.find(x => x.tag === 'figcaption').textContent, /重新搜索/);
  assert.ok(elements.find(x => x.tag === 'pre').textContent);
});

test('plain text sources remain readable without a fake page preview', async () => {
  const {elements} = await render({visual: null, source: '/pinned-source', source_is_pdf: false});
  assert.equal(elements.some(x => x.tag === 'img'), false);
  assert.equal(elements.find(x => x.href === '/pinned-source').textContent, '下载原文件');
  const legacy = await render(undefined);
  assert.ok(legacy.elements.find(x => x.tag === 'pre'));
});
