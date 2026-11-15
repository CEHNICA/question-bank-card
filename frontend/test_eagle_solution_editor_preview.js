const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const INDEX_HTML_PATH = path.join(__dirname, 'eagle', 'index.html');
const EDITOR_JS_PATH = path.join(__dirname, 'eagle-solution-editor.js');
const QB_RENDER_PATH = path.join(__dirname, 'qb-render.js');

/**
 * 构建支持事件监听、属性读写与 DOM 查询的轻量节点模拟器，供无浏览器环境下验证编辑器交互
 */
function createMockEnvironment() {
  class MockClassList {
    constructor(el) { this.el = el; }
    _set() { return new Set(String(this.el._className || '').split(/\s+/).filter(Boolean)); }
    add(...names) { const s = this._set(); names.forEach((n) => s.add(n)); this.el._className = [...s].join(' '); }
    remove(...names) { const s = this._set(); names.forEach((n) => s.delete(n)); this.el._className = [...s].join(' '); }
    toggle(name, force) {
      const s = this._set();
      const next = force !== undefined ? Boolean(force) : !s.has(name);
      if (next) s.add(name); else s.delete(name);
      this.el._className = [...s].join(' ');
      return next;
    }
    contains(name) { return this._set().has(name); }
  }

  function matchSingleSelector(el, sel) {
    if (!el || el.nodeType !== 1) return false;
    if (sel.startsWith('#')) return el.id === sel.slice(1);
    if (sel.startsWith('.')) return el.classList.contains(sel.slice(1));
    const attrMatch = /^([a-zA-Z0-9_-]*)\[([^=\]]+)(?:="([^"]*)")?\]$/.exec(sel);
    if (attrMatch) {
      const [, tag, attr, val] = attrMatch;
      if (tag && el.tagName.toLowerCase() !== tag.toLowerCase()) return false;
      const actual = el.getAttribute(attr);
      if (actual === null || actual === undefined) return false;
      return val === undefined ? true : String(actual) === val;
    }
    return el.tagName.toLowerCase() === sel.toLowerCase();
  }

  function matchSelector(el, selector) {
    return selector.split(',').some((part) => {
      const tokens = part.trim().split(/\s+/).filter(Boolean);
      if (!tokens.length) return false;
      if (!matchSingleSelector(el, tokens[tokens.length - 1])) return false;
      let cur = el.parentNode;
      for (let i = tokens.length - 2; i >= 0; i--) {
        while (cur && !matchSingleSelector(cur, tokens[i])) cur = cur.parentNode;
        if (!cur) return false;
        cur = cur.parentNode;
      }
      return true;
    });
  }

  class MockNode {
    constructor(nodeType, tagName = '') {
      this.nodeType = nodeType;
      this.tagName = tagName.toUpperCase();
      this.childNodes = [];
      this.parentNode = null;
      this.ownerDocument = null;
      this._attrs = {};
      this.style = {};
      this.dataset = new Proxy({}, {
        get: (_, key) => this._attrs['data-' + String(key).replace(/[A-Z]/g, (m) => '-' + m.toLowerCase())],
        set: (_, key, val) => {
          this._attrs['data-' + String(key).replace(/[A-Z]/g, (m) => '-' + m.toLowerCase())] = String(val);
          return true;
        },
      });
      this._listeners = {};
      this._className = '';
      this._innerHTML = '';
      this.classList = new MockClassList(this);
      this.value = '';
      this.selectionStart = 0;
      this.selectionEnd = 0;
      this.scrollTop = 0;
      this.scrollLeft = 0;
      this.clientLeft = 0;
      this.clientHeight = 300;
      this.scrollHeight = 96;
    }
    get className() { return this._className; }
    set className(v) { this._className = String(v || ''); }
    get id() { return this._attrs.id || ''; }
    set id(v) { this._attrs.id = String(v || ''); }
    get src() { return this._attrs.src || ''; }
    set src(v) { this._attrs.src = String(v || ''); }
    get alt() { return this._attrs.alt || ''; }
    set alt(v) { this._attrs.alt = String(v || ''); }
    get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
    setAttribute(k, v) {
      if (k === 'class') this._className = String(v);
      else this._attrs[k] = String(v);
      if (k === 'value') this.value = String(v);
    }
    getAttribute(k) {
      if (k === 'class') return this._className;
      return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null;
    }
    hasAttribute(k) { return k === 'class' ? Boolean(this._className) : Object.prototype.hasOwnProperty.call(this._attrs, k); }
    append(...nodes) {
      nodes.forEach((n) => {
        if (typeof n === 'string') n = this.ownerDocument.createTextNode(n);
        if (!n) return;
        n.parentNode = this;
        n.ownerDocument = this.ownerDocument;
        this.childNodes.push(n);
      });
    }
    appendChild(n) { this.append(n); return n; }
    replaceChildren(...nodes) { this.childNodes = []; this.append(...nodes); }
    remove() {
      if (!this.parentNode) return;
      this.parentNode.childNodes = this.parentNode.childNodes.filter((c) => c !== this);
      this.parentNode = null;
    }
    set innerHTML(html) {
      this._innerHTML = String(html || '');
      this.childNodes = parseSimpleHtml(this._innerHTML, this.ownerDocument, this);
      this.querySelectorAll('textarea').forEach((ta) => {
        ta.value = ta.textContent;
      });
    }
    get innerHTML() {
      if (!this.childNodes.length && this._innerHTML) return this._innerHTML;
      return this.childNodes.map((c) => (c.nodeType === 3 ? c.textContent : c.outerHTML)).join('');
    }
    get outerHTML() {
      const tag = this.tagName.toLowerCase();
      const attrs = [];
      if (this._className) attrs.push(`class="${this._className}"`);
      Object.entries(this._attrs).forEach(([k, v]) => attrs.push(`${k}="${v}"`));
      const attrStr = attrs.length ? ' ' + attrs.join(' ') : '';
      return `<${tag}${attrStr}>${this.innerHTML}</${tag}>`;
    }
    set textContent(t) {
      this._innerHTML = '';
      this.childNodes = [this.ownerDocument.createTextNode(String(t ?? ''))];
    }
    get textContent() {
      if (!this.childNodes.length && this._innerHTML) return this._innerHTML.replace(/<[^>]+>/g, '');
      return this.childNodes.map((c) => c.textContent).join('');
    }
    addEventListener(type, fn) {
      (this._listeners[type] ||= []).push(fn);
    }
    dispatchEvent(evt) {
      evt.target ||= this;
      evt.currentTarget = this;
      evt.preventDefault ||= () => {};
      evt.stopPropagation ||= () => {};
      let cur = this;
      while (cur) {
        (cur._listeners[evt.type] || []).forEach((fn) => fn.call(cur, evt));
        cur = cur.parentNode;
      }
    }
    closest(selector) {
      let cur = this;
      while (cur && cur.nodeType === 1) {
        if (matchSelector(cur, selector)) return cur;
        cur = cur.parentNode;
      }
      return null;
    }
    querySelectorAll(selector) {
      const out = [];
      const walk = (node) => {
        node.childNodes.forEach((c) => {
          if (c.nodeType === 1) {
            if (matchSelector(c, selector)) out.push(c);
            walk(c);
          }
        });
      };
      walk(this);
      return out;
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    contains(target) {
      let cur = target;
      while (cur) { if (cur === this) return true; cur = cur.parentNode; }
      return false;
    }
    getBoundingClientRect() { return { left: 0, top: 0, right: 100, bottom: 20, width: 100, height: 20 }; }
  }

  function parseSimpleHtml(html, doc, parent) {
    const roots = [];
    const stack = [parent];
    const tokenRe = /<(\/?)([a-zA-Z0-9-]+)([^>]*)>|([^<]+)/g;
    const voidTags = new Set(['img', 'input', 'br', 'hr']);
    let m;
    while ((m = tokenRe.exec(html)) !== null) {
      const top = stack[stack.length - 1];
      if (m[4]) {
        const text = m[4].replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
        const tn = doc.createTextNode(text);
        tn.parentNode = top;
        if (top === parent) roots.push(tn); else top.childNodes.push(tn);
        continue;
      }
      const isClose = Boolean(m[1]);
      const tag = m[2].toLowerCase();
      if (isClose) {
        if (stack.length > 1) stack.pop();
        continue;
      }
      const rawAttrs = m[3] || '';
      const selfClose = rawAttrs.endsWith('/') || voidTags.has(tag);
      const el = doc.createElement(tag);
      const attrRe = /([a-zA-Z0-9_:-]+)(?:="([^"]*)")?/g;
      let am;
      while ((am = attrRe.exec(rawAttrs)) !== null) {
        if (am[1] === '/') continue;
        el.setAttribute(am[1], am[2] ?? '');
      }
      el.parentNode = top;
      if (top === parent) roots.push(el); else top.childNodes.push(el);
      if (!selfClose) stack.push(el);
    }
    return roots;
  }

  const doc = {
    _listeners: {},
    activeElement: null,
    createElement(tag) {
      const n = new MockNode(1, tag);
      n.ownerDocument = doc;
      return n;
    },
    createTextNode(text) {
      return { nodeType: 3, textContent: String(text ?? ''), length: String(text ?? '').length, parentNode: null, ownerDocument: doc };
    },
    createRange() {
      return { setStart() {}, setEnd() {}, getClientRects() { return [{ left: 10, top: 10, width: 4, height: 18 }]; } };
    },
    addEventListener(type, fn) { (this._listeners[type] ||= []).push(fn); },
    dispatchEvent(evt) {
      evt.preventDefault ||= () => {};
      evt.stopPropagation ||= () => {};
      (this._listeners[evt.type] || []).forEach((fn) => fn(evt));
    },
  };

  const body = doc.createElement('div');
  const modal = doc.createElement('div'); modal.id = 'solutionEditorModal'; modal.className = 'hidden';
  const splitBtn = doc.createElement('button'); splitBtn.id = 'solModeSplitBtn';
  const colsBtn = doc.createElement('button'); colsBtn.id = 'solModeColumnsBtn';
  const statusEl = doc.createElement('span'); statusEl.id = 'solutionEditorStatus';
  const listEl = doc.createElement('div'); listEl.id = 'solutionEditorList';
  const tabsEl = doc.createElement('div'); tabsEl.id = 'solutionEditorTabs'; tabsEl.className = 'hidden';
  const colsEl = doc.createElement('div'); colsEl.id = 'solutionEditorColumns';
  modal.append(splitBtn, colsBtn, statusEl, listEl, tabsEl, colsEl);
  body.append(modal);
  doc.body = body;
  doc.getElementById = (id) => body.querySelector(`#${id}`);
  doc.querySelector = (sel) => body.querySelector(sel);
  doc.querySelectorAll = (sel) => body.querySelectorAll(sel);

  return { doc };
}

test('eagle/index.html 包含双栏实时预览与多题标签栏 DOM 契约', () => {
  const html = fs.readFileSync(INDEX_HTML_PATH, 'utf8');
  assert.ok(html.includes('id="solutionEditorModal"'));
  assert.ok(html.includes('id="solModeSplitBtn"'), '应存在双栏预览切换按钮');
  assert.ok(html.includes('id="solModeColumnsBtn"'), '应存在多题并排切换按钮');
  assert.ok(html.includes('id="solutionEditorTabs"'), '应存在多题 Tab 带容器');
  assert.ok(html.includes('/eagle-solution-editor.js?v=b5-20261115b'), '应刷新脚本版本号避免缓存旧界面');
  assert.ok(html.includes('.sol-live-preview') && html.includes('.qb-blank'), '应内置实时预览排版 CSS 样式');
  assert.ok(html.includes("if (typeof closeSolutionEditor === 'function') closeSolutionEditor();"), '保留 Escape 联动关闭');
});

test('答案解析编辑器：完整题面、实时公式预览、光标联动、多题切换、全部采纳与快捷键行为验证', async () => {
  const { doc } = createMockEnvironment();
  const fetchCalls = [];
  const katexCalls = [];
  const zoomedImages = [];
  const openedPapers = [];

  const questionsData = [
    {
      id: 'q1',
      questionNumber: '1',
      questionType: '单选题',
      questionTypeCode: 'single_choice',
      documentId: 'doc-1',
      sourcePageIdx: 2,
      source: '初三特殊平行四边形专题精选',
      rawStem: '如图，在菱形 $ABCD$ 中，若 $OE=3$，则周长为（ ）',
      rawOptions: { A: '$12$', B: '$18$', C: '$24$', D: '$30$' },
      rawFigures: [{ url: '/media/q1_rhombus.png', alt: '菱形图' }],
      rawAnswer: '',
      rawAnalysis: '',
      savedSolution: {},
    },
    {
      id: 'q2',
      questionNumber: '2',
      questionType: '解答题',
      questionTypeCode: 'solution',
      documentId: 'doc-1',
      sourcePageIdx: 3,
      source: '高一集合练习',
      rawStem: '已知集合 $A=\\{x|-1\\le x\\le 1\\}$，求值。',
      rawOptions: {},
      rawFigures: [],
      rawAnswer: '[-1,1]',
      rawAnalysis: '由不等式性质直接可得。',
      savedSolution: {},
    },
  ];

  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    FormData: class MockFormData {
      constructor() { this.fields = []; }
      append(k, v) { this.fields.push([k, v]); }
    },
    document: doc,
    window: null,
    API_BASE: '',
    questionsData,
    filteredQuestions: questionsData,
    basketQuestionIds: ['q1'],
    katex: {
      render(latex, node) {
        katexCalls.push(latex);
        node.innerHTML = `<span class="katex-rendered">${latex}</span>`;
      },
    },
    triggerKaTeX(el) { el.dataset.katexTriggered = '1'; },
    previewImageModal(url, title) { zoomedImages.push({ url, title }); },
    openOriginalPaperModal(docId, pageIdx, title, qid) { openedPapers.push({ docId, pageIdx, title, qid }); },
    fetch: async (url, init = {}) => {
      fetchCalls.push({ url, method: init.method || 'GET', body: init.body });
      if (url.includes('/api/library/q1/solution-images')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ figure: { id: 'fig-paste-1', url: '/media/paste1.png', display_width: 65, position: 'after', paragraph: null } }),
        };
      }
      if (url.endsWith('/api/library/q1/solution') && init.method === 'POST') {
        const parsed = JSON.parse(init.body);
        return {
          ok: true,
          status: 200,
          json: async () => ({ solution: parsed, base_revision: 1 }),
        };
      }
      if (url.includes('/api/library/q1/solution')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            base_revision: 0,
            solution: {
              answer: '',
              analysis: '',
              figures: [{ id: 'fig-1', url: '/media/sol1.png', display_width: 80, position: 'paragraph', paragraph: 0 }],
            },
            origin: { answer: 'C', analysis: '原卷参考：$OE=3$' },
            ai_answer: { answer: 'C', analysis: '第一段：连接对角线。\n\n第二段：得 $BC=2OE=6$，周长为 $24$。', model: 'MiniMax-M3.1' },
          }),
        };
      }
      if (url.includes('/api/library/q2/solution')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            base_revision: 0,
            solution: { answer: '[-1,1]', analysis: '解析 2', figures: [] },
            origin: { answer: '', analysis: '' },
            ai_answer: null,
          }),
        };
      }
      if (url.endsWith('/api/library/jobs') && init.method === 'POST') {
        return {
          ok: true,
          status: 200,
          json: async () => ({ queued: 1, jobs: [{ id: 'job-99', kind: 'answer', status: 'pending' }] }),
        };
      }
      if (url.includes('/api/library/jobs')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            jobs: [{ id: 'job-99', kind: 'answer', status: 'done', result: { answer: 'C', analysis: '最新生成 $4\\times 6=24$' } }],
          }),
        };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    },
  };
  sandbox.window = sandbox;

  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(QB_RENDER_PATH, 'utf8'), sandbox);
  sandbox.QB = sandbox.QBRender;
  vm.runInContext(fs.readFileSync(EDITOR_JS_PATH, 'utf8'), sandbox);

  // 1. 以默认双栏模式打开 q1 和 q2
  sandbox.openSolutionEditor(['q1', 'q2']);
  await new Promise((r) => setTimeout(r, 30));

  const listBox = doc.getElementById('solutionEditorList');
  assert.equal(listBox.dataset.katexTriggered, '1', '左侧选题列表应触发 KaTeX 渲染');

  const tabs = doc.getElementById('solutionEditorTabs');
  assert.ok(!tabs.classList.contains('hidden'), '打开题目后应显示顶部多题 Tab 带');
  assert.equal(tabs.querySelectorAll('[data-tab]').length, 2, '顶部 Tab 应包含 2 道已打开题目');

  const qPreview = doc.querySelector('[data-question-preview="q1"]');
  assert.ok(qPreview, '右侧上方应展示 q1 完整题面卡片');
  assert.ok(qPreview.innerHTML.includes('qb-options'), '完整题面应渲染选择题 A~D 选项');
  assert.ok(qPreview.innerHTML.includes('/media/q1_rhombus.png'), '完整题面应展示题干几何配图');

  // 点击题干配图应能触发灯箱放大
  const stemImg = qPreview.querySelector('img');
  assert.ok(stemImg);
  stemImg.dispatchEvent({ type: 'click' });
  assert.equal(zoomedImages.length, 1);

  // AI 初稿对照区应经过公式排版渲染而非裸源码
  assert.ok(katexCalls.includes('BC=2OE=6'), 'AI 初稿中的 LaTeX 公式应通过 KaTeX 渲染');
  assert.ok(doc.querySelector('[data-act="toggle-ai-raw"]'), '应支持查看源码与公式预览切换');

  // 点击「全部采纳」后，实时预览区应渲染段落、公式与第 1 段后配图
  const adoptBtn = doc.querySelector('[data-act="adopt-both"]');
  assert.ok(adoptBtn, '应提供全部采纳按钮');
  adoptBtn.dispatchEvent({ type: 'click' });

  const livePreview = doc.querySelector('[data-live-preview="q1"]');
  assert.ok(livePreview.innerHTML.includes('/media/sol1.png'), '实时预览应渲染解析配图');
  assert.ok(livePreview.innerHTML.includes('80mm'), '实时预览应按毫米尺寸展示配图');
  assert.ok(livePreview.innerHTML.includes('katex-rendered'), '实时预览应通过 KaTeX 渲染公式');

  // 验证解析配图顺序：第一段文本 -> sol1.png -> 第二段文本
  const htmlSnapshot = livePreview.innerHTML;
  assert.ok(
    htmlSnapshot.indexOf('第一段') < htmlSnapshot.indexOf('/media/sol1.png') &&
    htmlSnapshot.indexOf('/media/sol1.png') < htmlSnapshot.indexOf('第二段'),
    'position="paragraph" paragraph=0 的配图应排在第一段与第二段之间',
  );

  // 2. 在解析输入框内打字并移动光标，不销毁输入框 DOM 且联动高亮公式
  const anaTaBefore = doc.querySelector('textarea[data-field="analysis"]');
  anaTaBefore.value = '连接 $AC$，故 $OE=3$。';
  anaTaBefore.selectionStart = 12;
  anaTaBefore.selectionEnd = 12;
  anaTaBefore.dispatchEvent({ type: 'input' });

  const anaTaAfter = doc.querySelector('textarea[data-field="analysis"]');
  assert.strictEqual(anaTaAfter, anaTaBefore, '输入时不得整列重建 textarea 导致焦点与光标丢失');
  assert.ok(doc.querySelector('[data-live-preview="q1"] .qb-preview-active-formula'), '光标落在公式内时右侧预览应高亮该公式');
  assert.ok(doc.querySelector('[data-preview-hint]').textContent.includes('公式高亮联动'), '提示条应反馈公式高亮联动状态');

  // 3. 切换顶部 Tab 到 q2 后再切回 q1，未保存修改应完整保留
  tabs.querySelector('[data-tab="q2"]').dispatchEvent({ type: 'click' });
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(doc.querySelector('[data-question-preview="q2"]'), '切换 Tab 后主工作区应切为 q2');

  tabs.querySelector('[data-tab="q1"]').dispatchEvent({ type: 'click' });
  assert.equal(doc.querySelector('textarea[data-field="analysis"]').value, '连接 $AC$，故 $OE=3$。', '切回 q1 后应完整保留草稿');

  // 4. 切换「多题并排」模式后再切回「双栏预览」模式
  sandbox.setSolutionEditorMode('columns');
  assert.ok(doc.querySelector('[data-col="q1"]') && doc.querySelector('[data-col="q2"]'), '多题并排模式应同时展示 q1 与 q2');
  sandbox.setSolutionEditorMode('split');
  assert.ok(!doc.querySelector('[data-col="q2"]'), '双栏模式仅聚焦展示当前激活题');

  // 5. 测试 Ctrl+V 粘贴截图上传与实时渲染新图
  doc.activeElement = doc.querySelector('textarea[data-field="analysis"]');
  doc.dispatchEvent({
    type: 'paste',
    target: doc.activeElement,
    clipboardData: {
      items: [{ kind: 'file', type: 'image/png', getAsFile: () => ({ name: 'clip.png', size: 128 }) }],
    },
  });
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(doc.querySelector('[data-live-preview="q1"]').innerHTML.includes('/media/paste1.png'), 'Ctrl+V 粘贴截图上传后应在预览区立即显示');

  // 6. 测试 Ctrl+S 保存当前题
  doc.dispatchEvent({ type: 'keydown', key: 's', ctrlKey: true, shiftKey: false, altKey: false });
  await new Promise((r) => setTimeout(r, 20));
  const saveCall = fetchCalls.find((c) => c.url.endsWith('/api/library/q1/solution') && c.method === 'POST');
  assert.ok(saveCall, 'Ctrl+S 应触发保存当前题目解析 POST 请求');

  // 7. 测试编辑器内点击 AI 生成按钮使用规范 payload
  const genBtn = doc.querySelector('[data-act="ai-generate"]');
  genBtn.dispatchEvent({ type: 'click' });
  await new Promise((r) => setTimeout(r, 20));
  const jobPost = fetchCalls.find((c) => c.url.endsWith('/api/library/jobs') && c.method === 'POST');
  assert.ok(jobPost, '应发起 AI 解题任务请求');
  const jobPayload = JSON.parse(jobPost.body);
  assert.equal(jobPayload.kind, 'answer');
  assert.equal(jobPayload.solution_scope, true);
  assert.equal(jobPayload.executor, 'api');
  assert.deepEqual(jobPayload.ids, ['q1']);

  sandbox.closeSolutionEditor();
});
