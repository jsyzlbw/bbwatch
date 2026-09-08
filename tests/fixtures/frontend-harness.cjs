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
let clockNow = Date.parse('2026-09-08T02:00:00.000Z');
class ClockDate extends Date {
  constructor(...args) { super(...(args.length ? args : [clockNow])); }
  static now() { return clockNow; }
}
function scanStatus(state = 'idle', message = '') {
  return { id: state === 'idle' ? null : 'scan-1', state, message,
    started_at: state === 'idle' ? null : '2026-09-08T02:00:00.000Z',
    finished_at: ['succeeded', 'partial', 'failed'].includes(state)
      ? '2026-09-08T02:02:00.000Z' : null };
}
let inlineScan = null;
let inlineBoardMarkup = '';
let nextTimer = 0;
let fetchImpl = () => Promise.resolve({
  ok: true,
  status: 200,
  json: async () => ({ tasks: [], pending: [], hidden: [], last_scan: null, scan: scanStatus() }),
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
    this.listeners = new Map();
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
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(callback);
  }
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
  Date: ClockDate,
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
    timers.set(id, { type: 'timeout', callback, delay, due: clockNow + delay });
    return id;
  },
  setInterval: (callback, delay) => {
    const id = ++nextTimer;
    timers.set(id, { type: 'interval', callback, delay, due: clockNow + delay });
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
  scan: scanStatus(),
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
async function click(id) {
  const target = element(id);
  if (target.hidden || target.disabled) return;
  for (const callback of target.listeners.get('click') || [])
    await callback({ type: 'click', target });
  await new Promise(setImmediate);
}
async function pollOnce() {
  const entry = [...timers.entries()].find(([, timer]) =>
    timer.type === 'timeout' && timer.delay === 3000);
  if (!entry) return false;
  const [id, timer] = entry;
  timers.delete(id);
  clockNow = Math.max(clockNow, timer.due);
  await timer.callback();
  await new Promise(setImmediate);
  return true;
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
    active: run('SCAN_ACTIVE'),
    notice: element('noticeText').textContent,
    noticeVisible: !element('notice').hidden,
    retryLabel: element('retryBtn').textContent.trim(),
    retryVisible: !element('retryBtn').hidden,
    pollTimers: [...timers.values()].filter(t => t.type === 'timeout' && t.delay === 3000).length,
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
  const periodic = [...timers.values()].filter(t => t.type === 'interval' && t.delay === 60000);
  requests.length = 0;
  timers.clear();
  for (const node of elements.values()) {
    node.textContent = '';
    node.innerHTML = '';
  }
  element('retryBtn').textContent = html.match(/id="retryBtn"[^>]*>([\s\S]*?)<\/button>/)[1].trim();
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
  if (scenario === 'elapsed_times') {
    return [0, 1, 59, 60, 119, 3599, 3600, 7199, 86399, 86400, 172799].map(seconds => {
      setState({ ...structuredClone(initial), last_scan: new Date(clockNow - seconds * 1000).toISOString() });
      run('render()');
      return element('lastScan').textContent;
    });
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
    const empty = { tasks: [], pending: [], hidden: [], last_scan: null, scan: scanStatus() };
    setState(empty);
    fetchImpl = url => Promise.resolve({ ok: true, status: 200,
      json: async () => url === '/api/tasks' ? empty : { ok: true, scan: scanStatus('running') } });
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
  if (scenario.startsWith('retry_')) {
    const mode = scenario.slice(6);
    const server = { ...structuredClone(initial), scan: scanStatus(
      mode === 'partial' ? 'partial' : mode === 'running' ? 'running' : 'failed',
      '无法连接学校服务，请检查网络后重试。') };
    const response = data => ({ ok: true, status: 200,
      json: async () => structuredClone(data) });
    fetchImpl = () => Promise.resolve(response(server));
    await run('load()');
    if (mode === 'load_failure') {
      fetchImpl = () => failedResponse('network');
      await run('load()');
      const disconnected = snapshot();
      requests.length = 0;
      fetchImpl = () => Promise.resolve(response(server));
      await click('retryBtn');
      return { ...snapshot(), disconnected, expectedState: server };
    }
    if (mode === 'uncertain_post') {
      fetchImpl = () => failedResponse('network');
      await click('scanBtn');
      const uncertain = snapshot();
      requests.length = 0;
      server.scan = { ...scanStatus('running', '正在扫描课程。'), id: 'scan-2' };
      fetchImpl = () => Promise.resolve(response(server));
      await click('retryBtn');
      return { ...snapshot(), uncertain, expectedState: server };
    }
    requests.length = 0;
    if (mode === 'running') {
      await click('retryBtn');
      return snapshot();
    }
    const before = snapshot();
    let accept;
    fetchImpl = (url, options) => options.method === 'POST'
      ? new Promise(resolve => { accept = resolve; })
      : Promise.resolve(response(server));
    const firstClick = click('retryBtn');
    await click('retryBtn');
    const pending = snapshot();
    if (accept) accept(response({ ok: true,
      scan: { ...scanStatus('running', '正在扫描课程。'), id: 'scan-2' } }));
    await firstClick;
    return { ...snapshot(), before, pending, expectedState: server };
  }
  if (scenario.startsWith('flow_')) {
    const flow = scenario.slice(5);
    let server = { ...structuredClone(initial), scan: scanStatus('running', '正在扫描课程。') };
    let failGet = false;
    fetchImpl = (url, options) => {
      if (options.method === 'POST') return Promise.resolve({ ok: true, status: 200,
        json: async () => ({ ok: true, scan: scanStatus('running', '正在扫描课程。') }) });
      if (failGet) return failedResponse('network');
      return Promise.resolve({ ok: true, status: 200, json: async () => structuredClone(server) });
    };
    setState(structuredClone(initial));
    if (flow === 'reload_running') {
      setState(null);
      await run('load()');
      return snapshot();
    }
    if (flow === 'periodic' || flow === 'periodic_hidden') {
      server = { ...structuredClone(initial), last_scan: '2026-09-08T02:01:00.000Z' };
      document.hidden = flow === 'periodic_hidden';
      for (const timer of periodic) await timer.callback();
      await new Promise(setImmediate);
      return { ...snapshot(), expectedState: document.hidden ? initial : server };
    }
    if (flow === 'old_backend' || flow === 'stale_empty') {
      server = { ...structuredClone(initial), scan: scanStatus() };
      if (flow === 'old_backend') delete server.scan;
      else { server.tasks = []; server.pending = []; server.hidden = [];
        server.last_scan = '2026-08-23T02:00:00.000Z'; }
      await run('load()');
      if (flow === 'old_backend') await run('doScan()');
      return snapshot();
    }
    if (flow === 'launch_failure' || flow === 'legacy_post') {
      fetchImpl = () => Promise.resolve({ ok: true, status: 200, json: async () =>
        flow === 'legacy_post' ? { ok: true } : { ok: false,
          scan: { ...scanStatus('failed', '无法启动扫描，请检查本机运行环境。'), id: null } } });
      await run('doScan()');
      return snapshot();
    }
    if (flow === 'hide_pending_post') {
      let accept;
      const normalFetch = fetchImpl;
      fetchImpl = (url, options) => options.method === 'POST'
        ? new Promise(resolve => { accept = resolve; }) : normalFetch(url, options);
      const pending = run('doScan()');
      await dispatchWindow('pagehide');
      accept({ ok: true, status: 200, json: async () => ({ ok: true, scan: server.scan }) });
      await pending;
      const hidden = snapshot();
      await dispatchWindow('pageshow');
      return { ...snapshot(), hidden };
    }
    await run('doScan()');
    if (flow === 'restore_running') {
      await dispatchWindow('pagehide');
      const hidden = snapshot();
      await dispatchWindow('pageshow');
      return { ...snapshot(), hidden };
    }
    if (flow === 'long_running' || flow === 'long_completion') {
      let polls = 0;
      for (let i = 0; i < 22; i++) if (await pollOnce()) polls++;
      await run('doScan()');
      const beforeCompletion = snapshot();
      if (flow === 'long_completion') {
        server.scan = scanStatus('succeeded', '扫描已完成。');
        await pollOnce();
      }
      return { ...snapshot(), polls, beforeCompletion };
    }
    if (flow === 'wait_timeout' || flow === 'timeout_recovery') {
      clockNow += 16 * 60000 + 1;
      await pollOnce();
      run('render()');
      const expired = snapshot();
      for (const timer of periodic) await timer.callback();
      const stillRunning = snapshot();
      if (flow === 'timeout_recovery') {
        server.scan = scanStatus('succeeded', '扫描已完成。');
        for (const timer of periodic) await timer.callback();
      }
      return { ...snapshot(), expired, stillRunning };
    }
    let duringFailure;
    if (flow === 'recover_get') {
      failGet = true;
      await pollOnce();
      duringFailure = snapshot();
      failGet = false;
    }
    if (flow === 'unrelated_terminal') {
      server.scan = { ...scanStatus('succeeded', 'Earlier scan finished.'), id: 'different-scan' };
      await pollOnce();
      for (const timer of periodic) await timer.callback();
      return snapshot();
    }
    if (flow === 'newer_running') {
      server.scan = { ...scanStatus('running', '正在扫描另一批课程。'), id: 'newer-scan' };
      await pollOnce();
      const newer = snapshot();
      server.scan = { ...scanStatus('succeeded', '新的扫描已完成。'), id: 'newer-scan' };
      await pollOnce();
      return { ...snapshot(), newer };
    }
    const terminal = flow === 'failed' ? 'failed' : flow === 'partial' ? 'partial' : 'succeeded';
    server.scan = scanStatus(terminal, terminal === 'failed'
      ? '扫描失败，请重新验证登录状态。' : terminal === 'partial'
        ? '扫描部分完成，仍有课程无法读取。' : '扫描已完成。');
    await pollOnce();
    run('render()');
    return { ...snapshot(), duringFailure, expectedState: server };
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
