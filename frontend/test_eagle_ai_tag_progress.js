// @ts-check
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const eagleHtmlPath = path.join(__dirname, "eagle", "index.html");
const eagleHtml = fs.readFileSync(eagleHtmlPath, "utf8");

/**
 * 构建用于测试右栏 AI 标签推荐进度与耗时展示的轻量沙盒
 */
function createTagProgressSandbox(fetchImpl) {
  const elementsById = new Map();
  const makeMockEl = (id) => {
    const classSet = new Set();
    return {
      id,
      innerText: "",
      textContent: "",
      innerHTML: "",
      value: "",
      className: "",
      children: [],
      style: {},
      classList: {
        add: (...cls) => cls.forEach((c) => classSet.add(c)),
        remove: (...cls) => cls.forEach((c) => classSet.delete(c)),
        contains: (c) => classSet.has(c),
        toggle: (c) => (classSet.has(c) ? classSet.delete(c) : classSet.add(c)),
      },
      appendChild: (c) => c,
      insertBefore: (c) => c,
      querySelectorAll: () => [],
      addEventListener: () => {},
      setAttribute: () => {},
      getAttribute: () => "",
    };
  };

  [
    "inspectorTitle",
    "inspectorBadge",
    "currentTagsContainer",
    "pinnedTagsContainer",
    "aiSuggestedTagsContainer",
    "questionMetaCard",
    "metaDifficultyStars",
    "metaFavoriteBtn",
    "metaFavoriteIcon",
    "metaFavoriteText",
    "metaMinutes",
    "metaType",
    "metaSource",
    "metaOneNoteBox",
    "toastContainer",
    "aiTagElapsedText",
    "aiTagStatusHint",
  ].forEach((id) => elementsById.set(id, makeMockEl(id)));

  const storageMap = new Map();
  const localStorage = {
    getItem: (k) => (storageMap.has(k) ? storageMap.get(k) : null),
    setItem: (k, v) => storageMap.set(String(k), String(v)),
    removeItem: (k) => storageMap.delete(String(k)),
  };

  const timers = [];
  const sandbox = {
    console,
    Math,
    Date,
    Set,
    Map,
    Array,
    Object,
    JSON,
    String,
    Number,
    Boolean,
    RegExp,
    Error,
    Promise,
    parseInt,
    parseFloat,
    isNaN,
    encodeURIComponent,
    decodeURIComponent,
    setTimeout: (fn, ms) => {
      if (ms !== 1000) return setTimeout(fn, 1);
      const id = timers.length + 1;
      timers.push({ id, fn, ms, cleared: false });
      return id;
    },
    clearTimeout: (id) => {
      const t = timers.find((item) => item.id === id);
      if (t) t.cleared = true;
    },
    document: {
      getElementById: (id) => elementsById.get(id) || null,
      querySelectorAll: () => [],
      createElement: (tag) => makeMockEl(`__created_${tag}`),
      createDocumentFragment: () => ({ children: [], appendChild: (c) => c }),
      addEventListener: () => {},
      body: makeMockEl("body"),
    },
    localStorage,
    IntersectionObserver: function () {
      return { observe: () => {}, unobserve: () => {}, disconnect: () => {} };
    },
    fetch: fetchImpl || (async () => ({ ok: true, json: async () => ({ jobs: [] }) })),
    URL: { createObjectURL: () => "", revokeObjectURL: () => {} },
    Blob: function () {},
    crypto: { randomUUID: () => "00000000-0000-4000-8000-000000000001" },
  };
  sandbox.window = {
    location: { href: "http://127.0.0.1:8769/eagle", origin: "http://127.0.0.1:8769", search: "" },
    addEventListener: () => {},
    localStorage,
    document: sandbox.document,
  };
  sandbox.globalThis = sandbox;

  const scripts = [...eagleHtml.matchAll(/<script(?![^>]*src=)[^>]*>([\s\S]*?)<\/script>/gi)];
  assert.ok(scripts.length > 0, "eagle/index.html 必须包含内联脚本");
  vm.createContext(sandbox);
  for (const m of scripts) {
    vm.runInContext(m[1], sandbox);
  }
  return { sandbox, elementsById, timers };
}

test("Eagle 右栏 AI 标签推荐：实时状态、计时与重连轮询", async (t) => {
  await t.test("1. formatAiElapsed 正确格式化耗时文本", () => {
    const { sandbox } = createTagProgressSandbox();
    assert.equal(sandbox.formatAiElapsed(0), "00:00");
    assert.equal(sandbox.formatAiElapsed(12400), "00:12");
    assert.equal(sandbox.formatAiElapsed(65000), "01:05");
    assert.equal(sandbox.formatAiElapsed(3661000), "01:01:01");
  });

  await t.test("2. 后端处于 queued 且超过 15 秒时显示排队状态、耗时与 worker 提示并禁用重生成按钮", () => {
    const queuedAt = new Date(Date.now() - 22000).toISOString();
    const { sandbox, elementsById } = createTagProgressSandbox(async () => ({
      ok: true,
      json: async () => ({
        jobs: [{ id: "job-1", kind: "tags", status: "queued", created_at: queuedAt, updated_at: queuedAt }],
      }),
    }));

    vm.runInContext(
      `
      questionsData.push({
        id: 'q-1',
        questionNumber: 1,
        questionType: '解答题',
        difficultyStars: 3,
        source: '测试卷',
        tags: [],
        tagSuggestion: {
          id: 'sug-1',
          job_id: 'job-1',
          status: 'queued',
          created_at: '${queuedAt}',
          updated_at: '${queuedAt}',
          suggestions: []
        }
      });
      currentActiveQuestionId = 'q-1';
      aiTagPollingPausedQuestions.add('q-1');
      renderRightInspector();
      `,
      sandbox
    );

    const html = elementsById.get("aiSuggestedTagsContainer").innerHTML;
    assert.match(html, /排队等待处理…/, "queued 状态应明确显示排队等待处理");
    assert.match(html, /id="aiTagElapsedText"[^>]*>已耗时 00:2[123]/, "应显示已耗时计时器");
    assert.match(html, /run_worker/, "排队超过 15 秒应提示检查后台工作者 run_worker");
    assert.match(html, /id="aiTagRegenBtn"[^>]*disabled[^>]*>⏳ 排队中…/, "排队期间应禁用重新生成按钮");
  });

  await t.test("3. 后端处于 running 时显示正在生成与耗时", () => {
    const startedAt = new Date(Date.now() - 6000).toISOString();
    const { sandbox, elementsById } = createTagProgressSandbox();

    vm.runInContext(
      `
      questionsData.push({
        id: 'q-2',
        questionNumber: 2,
        questionType: '选择题',
        difficultyStars: 2,
        source: '测试卷',
        tags: [],
        tagSuggestion: {
          id: 'sug-2',
          job_id: 'job-2',
          status: 'running',
          created_at: '${startedAt}',
          updated_at: '${startedAt}',
          suggestions: []
        }
      });
      currentActiveQuestionId = 'q-2';
      aiTagPollingPausedQuestions.add('q-2');
      renderRightInspector();
      `,
      sandbox
    );

    const html = elementsById.get("aiSuggestedTagsContainer").innerHTML;
    assert.match(html, /AI 正在生成标签建议…/, "running 状态应显示正在生成");
    assert.match(html, /id="aiTagElapsedText"[^>]*>已耗时 00:0[567]/, "running 状态应显示已耗时");
    assert.match(html, /id="aiTagRegenBtn"[^>]*disabled[^>]*>⏳ 生成中…/, "生成中应禁用重新生成按钮");
  });

  await t.test("4. 点击重新生成若命中已有活跃任务可自动挂载轮询并在完成后展示建议标签", async () => {
    let pollStep = 0;
    const nowIso = new Date().toISOString();
    const mockFetch = async (url, init) => {
      if (url.endsWith("/api/library/jobs") && init && init.method === "POST") {
        // 模拟后端已有活跃任务而 skipped=1、jobs=[] 的场景
        return { ok: true, json: async () => ({ queued: 0, skipped: 1, jobs: [] }) };
      }
      if (url.includes("/api/library/jobs?ids=")) {
        pollStep += 1;
        const status = pollStep === 1 ? "running" : "done";
        return {
          ok: true,
          json: async () => ({
            jobs: [
              {
                id: "job-existing",
                kind: "tags",
                status,
                created_at: nowIso,
                updated_at: nowIso,
                started_at: nowIso,
              },
            ],
          }),
        };
      }
      if (url.includes("/api/library/q-3")) {
        return {
          ok: true,
          json: async () => ({
            publication: {
              id: "q-3",
              tag_assignments: [],
              tag_revision: 1,
              tag_suggestion: {
                id: "sug-3",
                status: "pending",
                suggestions: [{ tag_id: "t-1", name: "导数极值", kind: "knowledge" }],
              },
            },
          }),
        };
      }
      return { ok: true, json: async () => ({}) };
    };

    const { sandbox, elementsById } = createTagProgressSandbox(mockFetch);
    vm.runInContext(
      `
      questionsData.push({
        id: 'q-3',
        questionNumber: 3,
        questionType: '解答题',
        difficultyStars: 4,
        source: '测试卷',
        tags: [],
        tagSuggestion: null
      });
      currentActiveQuestionId = 'q-3';
      renderRightInspector();
      `,
      sandbox
    );

    await sandbox.regenerateTagSuggestions("q-3");

    const html = elementsById.get("aiSuggestedTagsContainer").innerHTML;
    assert.match(html, /✓ 导数极值/, "任务完成后应在右栏渲染待采纳标签建议");
    assert.match(html, /↻ 重新生成建议/, "任务完成后应恢复重新生成按钮");
  });

  await t.test("5. tickAiTagElapsedDisplay 实时刷新计时与超时提示，失败/无匹配状态清晰可见", () => {
    const queuedAt = new Date(Date.now() - 16000).toISOString();
    const { sandbox, elementsById } = createTagProgressSandbox();

    vm.runInContext(
      `
      questionsData.push({
        id: 'q-4',
        questionNumber: 4,
        questionType: '填空题',
        difficultyStars: 3,
        source: '测试卷',
        tags: [],
        tagSuggestion: {
          id: 'sug-4',
          job_id: 'job-4',
          status: 'queued',
          created_at: '${queuedAt}',
          updated_at: '${queuedAt}',
          suggestions: []
        }
      });
      currentActiveQuestionId = 'q-4';
      aiTagPollingPausedQuestions.add('q-4');
      tickAiTagElapsedDisplay();
      `,
      sandbox
    );

    assert.match(elementsById.get("aiTagElapsedText").innerText, /^已耗时 00:1[567]/);
    assert.match(elementsById.get("aiTagStatusHint").innerText, /run_worker/);
    assert.equal(elementsById.get("aiTagStatusHint").classList.contains("hidden"), false);

    vm.runInContext(
      `
      questionsData[0].tagSuggestion = { status: 'failed', message: 'API 超时' };
      renderRightInspector();
      `,
      sandbox
    );
    assert.match(elementsById.get("aiSuggestedTagsContainer").innerHTML, /上次生成没成功：API 超时/);

    vm.runInContext(
      `
      questionsData[0].tagSuggestion = { status: 'no_match', suggestions: [] };
      renderRightInspector();
      `,
      sandbox
    );
    assert.match(elementsById.get("aiSuggestedTagsContainer").innerHTML, /AI 未匹配到合适标签/);
  });
});
