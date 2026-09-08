// Run the shipped, dependency-free dashboard script without network or live timers.
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(process.argv[2], 'utf8');
const scenario = process.argv[3];
const scripts = [...html.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/gi)];
const elements = new Map();
const timers = new Map();
const requests = [];
const windowListeners = new Map();
let inlineScan = null;
let inlineBoardMarkup = '';
let nextTimer = 0;
let fetchImpl = () => Promise.resolve({
  ok: true,
  status: 200,
  json: async () => ({ tasks: [], pending: [], hidden: [], last_scan: null }),
});

class Element {
  constructor(id = '') {
    this.id = id;
    this.innerHTML = '';
    this.textContent = '';
    this.className = '';
    this.disabled = false;
    this.hidden = false;
    this.style = {};
    this.dataset = {};
    this.attributes = {};
    this.classList = {
      contains: value => this.className.split(/\s+/).includes(value),
      add: (...values) => {
        this.className = [...new Set([...this.className.split(/\s+/), ...values])]
          .filter(Boolean).join(' ');
      },
      remove: (...values) => {
        this.className = this.className.split(/\s+/)
          .filter(value => !values.includes(value)).join(' ');
      },
      toggle: (value, force) => {
        const add = force === undefined ? !this.classList.contains(value) : force;
        this.classList[add ? 'add' : 'remove'](value);
        return add;
      },
    };
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(value) {
    this._innerHTML = String(value);
    this._textContent = String(value).replace(/<[^>]*>/g, '');
  }
  get textContent() { return this._textContent; }
  set textContent(value) {
    this._textContent = String(value);
    this._innerHTML = String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;');
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener() {}
  querySelector() { return null; }
  querySelectorAll() { return []; }
  scrollIntoView() {}
  focus() {}
}

function element(id) {
  if (!elements.has(id)) elements.set(id, new Element(id));
  return elements.get(id);
}
for (const match of html.matchAll(/\bid=["']([^"']+)["']/g)) element(match[1]);
const root = element('documentElement');
root.setAttribute('data-theme', 'light');
const document = {
  documentElement: root,
  body: element('body'),
  activeElement: null,
  getElementById: id => element(id),
  querySelector: selector => element(selector.replace(/^#/, '')),
  querySelectorAll: selector => selector === '[data-action="scan"]' && inlineScan
    ? [inlineScan] : [],
  addEventListener() {},
  createElement: tag => new Element(tag),
};
const context = vm.createContext({
  document,
  console,
  AbortController,
  URL,
  URLSearchParams,
  location: { search: '', hash: '', href: 'http://127.0.0.1:8840/' },
  navigator: { onLine: true },
  localStorage: { getItem: () => null, setItem() {} },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  fetch: (url, options = {}) => {
    requests.push({ url, ...options });
    return fetchImpl(url, options);
  },
  setTimeout: (callback, delay) => {
    const id = ++nextTimer;
    timers.set(id, { type: 'timeout', callback, delay });
    return id;
  },
  setInterval: (callback, delay) => {
    const id = ++nextTimer;
    timers.set(id, { type: 'interval', callback, delay });
    return id;
  },
  clearTimeout: id => timers.delete(id),
  clearInterval: id => timers.delete(id),
  requestAnimationFrame: callback => callback(),
  addEventListener(name, callback) {
    if (!windowListeners.has(name)) windowListeners.set(name, []);
    windowListeners.get(name).push(callback);
  },
});
context.window = context;
vm.runInContext(scripts.at(-1)[1], context, { timeout: 1000 });

const sample = {
  entity_key: 'col:_course_1:_assignment_7',
  course_id: '_course_1',
  content_id: '_content_9',
  course: 'CSC1001:<script>unsafe</script>',
  name: 'Assignment <script>alert(1)</script> & "review"',
  due_utc: '2026-09-10T15:59:00.000Z',
  done: false,
};
const initial = {
  tasks: [{ ...sample }],
  pending: [{ ...sample, entity_key: 'col:_course_1:_pending_8', waited_days: 18 }],
  hidden: [{ ...sample, entity_key: 'col:_course_1:_hidden_9' }],
  last_scan: '2026-09-08T01:00:00.000Z',
};

function run(source) { return vm.runInContext(source, context, { timeout: 1000 }); }
function setState(value) { context.fixture = value; run('STATE = fixture'); }
function materializeInlineScan() {
  const markup = element('board').innerHTML;
  if (inlineScan && markup === inlineBoardMarkup) return inlineScan;
  const match = markup.match(/<button\b([^>]*\bdata-action=["']scan["'][^>]*)>([\s\S]*?)<\/button>/);
  if (!match) throw new Error('Expected the empty-board scan button');
  inlineScan = new Element('inlineScan');
  inlineScan.innerHTML = match[2];
  inlineScan.disabled = /\bdisabled\b/.test(match[1]);
  inlineScan.dataset.action = 'scan';
  inlineBoardMarkup = markup;
  return inlineScan;
}
async function dispatchWindow(name) {
  for (const callback of windowListeners.get(name) || []) await callback({ type: name, persisted: true });
  await new Promise(setImmediate);
}
function snapshot() {
  return {
    state: JSON.parse(run('JSON.stringify(STATE)')),
    requests,
    text: [...elements.values()].filter(e => !e.hidden)
      .map(e => `${e.textContent} ${e.innerHTML}`).join('\n'),
    scanDisabled: element('scanBtn').disabled,
    scanSpinning: element('scanBtn').classList.contains('spin'),
    scanLabel: element('scanTxt').textContent,
    intervalCount: [...timers.values()].filter(t => t.type === 'interval').length,
  };
}
function failedResponse(mode) {
  if (mode === 'network') return Promise.reject(new Error('Connection unavailable'));
  return Promise.resolve({
    ok: false,
    status: 503,
    json: async () => ({ error: 'Service unavailable' }),
    text: async () => 'Service unavailable',
  });
}

async function main() {
  // Drain the initial load before the scenario, including any single-flight guard.
  await new Promise(setImmediate);
  requests.length = 0;
  timers.clear();
  for (const node of elements.values()) {
    node.textContent = '';
    node.innerHTML = '';
  }
  if (scenario === 'helpers') {
    context.sample = sample;
    return {
      times: run(`[
        fmt(parseUTC('2026-09-08T16:05:00Z')),
        fmt(parseUTC('2026-09-08T16:05:00.123456+00:00')),
        fmt(parseUTC('2026-12-31T23:59:00.000Z'))
      ]`),
      links: run(`[
        jumpUrl({...sample, course_id: '_c &1', content_id: '_x?2'}),
        jumpUrl({...sample, course_id: '', content_id: ''})
      ]`),
      task: run('taskRow(sample, 0)'),
      done: run('taskRow({...sample, done: true}, 0)'),
      pending: run('pendingRow(sample, 0)'),
      hidden: run('hiddenRow(sample, 0)'),
    };
  }
  if (scenario.startsWith('load_')) {
    const [, state, failure] = scenario.split('_');
    setState(state === 'initial' ? null : structuredClone(initial));
    fetchImpl = () => failedResponse(failure);
    await run('load()');
    return { ...snapshot(), expectedState: state === 'initial' ? null : initial };
  }
  if (scenario.startsWith('malformed_')) {
    const [, state, group, kind] = scenario.split('_');
    const data = structuredClone(initial);
    const invalid = { ...sample };
    if (kind === 'key') delete invalid.entity_key;
    if (kind === 'name') delete invalid.name;
    data[group] = [kind === 'null' ? null : kind === 'string' ? 'invalid row' : invalid];
    setState(state === 'initial' ? null : structuredClone(initial));
    fetchImpl = () => Promise.resolve({ ok: true, status: 200, json: async () => data });
    await run('load()');
    return { ...snapshot(), expectedState: state === 'initial' ? null : initial };
  }
  if (scenario === 'reset_inline_scan' || scenario === 'restore_scan_page') {
    const empty = { tasks: [], pending: [], hidden: [], last_scan: null };
    setState(empty);
    fetchImpl = url => Promise.resolve({ ok: true, status: 200,
      json: async () => url === '/api/tasks' ? empty : { ok: true } });
    if (scenario === 'reset_inline_scan') run('setScanBusy(true)');
    else await run('doScan()');
    run('render()');
    const busyButton = materializeInlineScan();
    const before = { disabled: busyButton.disabled, label: busyButton.textContent };
    if (scenario === 'reset_inline_scan') run('setScanBusy(false)');
    else {
      await dispatchWindow('pagehide');
      await dispatchWindow('pageshow');
    }
    const button = materializeInlineScan();
    return { ...snapshot(), before, active: run('SCAN_ACTIVE'),
      inlineDisabled: button.disabled, inlineLabel: button.textContent,
      pollTimers: [...timers.values()].filter(t => t.delay === 3000).length };
  }
  if (scenario.startsWith('mutation_')) {
    const [, operation, failure] = scenario.split('_');
    setState(structuredClone(initial));
    fetchImpl = () => failedResponse(failure);
    const source = {
      done: `toggle('${sample.entity_key}', true)`,
      hide: `hide('${sample.entity_key}', true)`,
      restore: `hide('col:_course_1:_hidden_9', false)`,
    }[operation];
    await run(source);
    return { ...snapshot(), expectedState: initial };
  }
  if (scenario.startsWith('success_')) {
    const operation = scenario.split('_')[1];
    setState(structuredClone(initial));
    const updated = structuredClone(initial);
    const key = operation === 'restore' ? initial.hidden[0].entity_key
      : operation === 'pending' ? initial.pending[0].entity_key : sample.entity_key;
    if (operation === 'done') updated.tasks[0].done = true;
    if (operation === 'hide') updated.hidden.push(...updated.tasks.splice(0, 1));
    if (operation === 'pending') updated.hidden.push(...updated.pending.splice(0, 1));
    // The server knows that this restored assignment belongs in pending.
    if (operation === 'restore') updated.pending.push(...updated.hidden.splice(0, 1));
    let resolveResponse;
    fetchImpl = url => url === '/api/tasks'
      ? Promise.resolve({ ok: true, status: 200, json: async () => structuredClone(updated) })
      : new Promise(resolve => { resolveResponse = resolve; });
    const source = operation === 'done' ? `toggle('${key}', true)`
      : `hide('${key}', ${operation !== 'restore'})`;
    const pending = run(source);
    const beforeResponse = snapshot().state;
    resolveResponse({ ok: true, status: 200, json: async () => ({ ok: true }) });
    await pending;
    return { ...snapshot(), beforeResponse, initial, expectedState: updated, key };
  }
  if (scenario.startsWith('scan_')) {
    setState(structuredClone(initial));
    fetchImpl = () => failedResponse(scenario.split('_')[1]);
    await run('doScan()');
    return snapshot();
  }
  if (scenario === 'render') {
    setState({ ...structuredClone(initial), tasks: [...initial.tasks, { ...sample,
      entity_key: 'col:_course_1:_done_10', done: true }] });
    run('render()');
    return snapshot();
  }
  throw new Error(`Unknown scenario: ${scenario}`);
}

main().then(result => process.stdout.write(JSON.stringify(result)))
  .catch(error => { console.error(error); process.exitCode = 1; });
