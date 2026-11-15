"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const eagleHtmlPath = path.resolve(__dirname, "eagle/index.html");
const eagleHtml = fs.readFileSync(eagleHtmlPath, "utf8");

test("Eagle 工作台：右侧详情页评分与列表星标交互规范", () => {
  // 1. DOM 结构与文案精简检查
  assert.ok(
    eagleHtml.includes('id="metaDifficultyStars"'),
    "必须包含右侧详情页评分星形容器 #metaDifficultyStars"
  );
  assert.ok(
    eagleHtml.includes('role="group"') && eagleHtml.includes('aria-label="评分调节"'),
    "评分星形必须符合可访问性 role 与 aria-label 属性规范"
  );
  assert.ok(
    eagleHtml.includes('>评分:</span>') && !eagleHtml.includes('点击打分'),
    "右侧考题属性卡片应只保留简洁的“评分:”标签，不显示冗余的“点击打分”文字"
  );
  assert.ok(
    !eagleHtml.includes('id="metaDifficultyBadge"'),
    "右侧评分星形后不应再显示冗余的“5★ / 未评分”文字徽标"
  );
  assert.ok(
    !eagleHtml.includes('id="metaMinutes"'),
    "右侧考题属性卡片不应再包含“建议耗时”(#metaMinutes) 行"
  );

  // 2. 关键函数存在性检查
  assert.ok(
    eagleHtml.includes("function renderInspectorDifficultyStars("),
    "必须实现 renderInspectorDifficultyStars 动态渲染 5 颗可点击评分星星"
  );
  assert.ok(
    eagleHtml.includes("function setQuestionDifficultyFromInspector("),
    "必须实现 setQuestionDifficultyFromInspector 处理点击评分与再次点击取消逻辑"
  );
  assert.ok(
    eagleHtml.includes("function updateQuestionCardDifficultyDisplay("),
    "必须实现 updateQuestionCardDifficultyDisplay 原地更新列表卡片星标"
  );
  assert.ok(
    eagleHtml.includes("function saveDifficultyOverride("),
    "必须实现 saveDifficultyOverride 持久化保存评分"
  );

  // 3. 默认未评分与按需显星规则检查
  assert.ok(
    eagleHtml.includes("未评分") && eagleHtml.includes("(current === clicked) ? 0 : clicked"),
    "必须支持未评分状态展示，以及再次点击当前已选星级时取消评分（回到 0 星）"
  );
  assert.ok(
    eagleHtml.includes("q.difficultyStars > 0 ? '★'.repeat(q.difficultyStars) : ''"),
    "列表卡片与紧凑行在未评分（0 星）时不应显示星星，仅评过分（1~5 星）时显示对应数量的 ★"
  );
  assert.ok(
    eagleHtml.includes(".q-difficulty-stars"),
    "题卡与紧凑行必须标记 .q-difficulty-stars 类以支持无缝原地更新"
  );
  assert.ok(
    eagleHtml.includes("hoverInspectorDifficultyStars"),
    "必须包含鼠标划过星形时的即时预览动效"
  );
});

test("Eagle 工作台：评分打分、再点取消评分与持久化运行行为测试", () => {
  const startMarker = "// ================= 试题评分持久化与右侧详情页打分交互 =================";
  const endMarker = "window.setQuestionDifficultyFromInspector = setQuestionDifficultyFromInspector;";
  const startIdx = eagleHtml.indexOf(startMarker);
  const endIdx = eagleHtml.indexOf(endMarker);
  assert.ok(startIdx !== -1 && endIdx > startIdx, "应能定位评分交互函数代码段");
  const snippet = eagleHtml.slice(startIdx, endIdx);

  const storage = new Map();
  const starsSpan = {
    innerHTML: "",
    classList: { add: () => {}, remove: () => {} },
  };
  const metaBadge = { className: "", textContent: "" };
  const metaDifficultyStars = {
    innerHTML: "",
    querySelectorAll: () => [],
  };
  const cardEl = {
    querySelector: (sel) => (sel === ".q-difficulty-stars" ? starsSpan : null),
  };
  const toasts = [];

  const sandbox = {
    Math,
    parseInt,
    setTimeout: (fn) => fn(),
    localStorage: {
      getItem: (k) => (storage.has(k) ? storage.get(k) : null),
      setItem: (k, v) => storage.set(k, String(v)),
    },
    document: {
      getElementById: (id) => {
        if (id === "metaDifficultyStars") return metaDifficultyStars;
        if (id === "metaDifficultyBadge") return metaBadge;
        if (id === "qcard_q1") return cardEl;
        return null;
      },
    },
    questionsData: [{ id: "q1", questionNumber: 1, difficultyStars: 0, difficulty: "" }],
    currentActiveQuestionId: "q1",
    filterState: { sortBy: "number", difficultyStars: [], difficultyMinStar: null },
    applyFilters: () => {},
    showToast: (msg, type) => toasts.push({ msg, type }),
  };

  vm.createContext(sandbox);
  vm.runInContext(snippet, sandbox);

  // 初始未评分：仅渲染 5 个灰星形按钮，不附加多余文字徽标
  sandbox.renderInspectorDifficultyStars(sandbox.questionsData[0]);
  assert.ok(!metaDifficultyStars.innerHTML.includes("metaDifficultyBadge"), "不应渲染多余的评分文字徽标");
  assert.equal(metaDifficultyStars.innerHTML.match(/data-star="/g)?.length, 5, "右侧详情栏应固定显示 5 颗可点星");

  // 点击第 4 颗星：打 4 星，列表卡片展示 ★★★★，写入 localStorage
  sandbox.setQuestionDifficultyFromInspector("q1", 4);
  assert.equal(sandbox.questionsData[0].difficultyStars, 4);
  assert.equal(sandbox.questionsData[0].difficulty, "★★★★");
  assert.equal(starsSpan.innerHTML, "★★★★");
  assert.equal(JSON.parse(storage.get("qb-difficulty-overrides")).q1, 4);
  assert.ok(toasts.at(-1).msg.includes("评为 4★"));

  // 再次点击第 4 颗星：取消评分（回到 0 星），列表卡片清空星星，移除持久化记录
  sandbox.setQuestionDifficultyFromInspector("q1", 4);
  assert.equal(sandbox.questionsData[0].difficultyStars, 0);
  assert.equal(sandbox.questionsData[0].difficulty, "");
  assert.equal(starsSpan.innerHTML, "");
  assert.equal(JSON.parse(storage.get("qb-difficulty-overrides")).q1, undefined);
  assert.ok(toasts.at(-1).msg.includes("已取消"));
});
