"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const eaglePath = path.join(__dirname, "eagle", "index.html");
const eagleHtml = fs.readFileSync(eaglePath, "utf8");

function extractInlineScript(html) {
  const scripts = [...html.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
  const inline = scripts.filter((m) => !/\bsrc\s*=/.test(m[1])).map((m) => m[2]);
  return inline.join("\n;\n");
}

function extractFunctionSource(source, name) {
  const marker = `function ${name}(`;
  const idx = source.indexOf(marker);
  assert.ok(idx >= 0, `Could not find ${marker} in eagle/index.html`);
  let depth = 0;
  let started = false;
  for (let i = idx; i < source.length; i++) {
    const ch = source[i];
    if (ch === "{") {
      depth++;
      started = true;
    } else if (ch === "}") {
      depth--;
      if (started && depth === 0) {
        return source.slice(idx, i + 1);
      }
    }
  }
  throw new Error(`Unbalanced braces for ${name}`);
}

test("Eagle UI places exclude-used controls inside #filterPillsPanel and keeps tag hover clean", () => {
  // 1. All inline scripts parse cleanly
  const scriptSrc = extractInlineScript(eagleHtml);
  new vm.Script(scriptSrc);

  // 2. excludeUsedSelect & btnUsageHistoryModal live inside #filterPillsPanel, not in the top toolbar above it
  const panelStart = eagleHtml.indexOf('id="filterPillsPanel"');
  const activeStripStart = eagleHtml.indexOf('id="activeFilterStrip"');
  const listStart = eagleHtml.indexOf('id="questionListContainer"');
  assert.ok(panelStart > activeStripStart && activeStripStart > 0);
  assert.ok(listStart > panelStart);
  const beforePanel = eagleHtml.slice(0, panelStart);
  assert.equal(beforePanel.includes('id="excludeUsedSelect"'), false, "excludeUsedSelect should no longer sit in top toolbar");
  assert.equal(beforePanel.includes('id="btnUsageHistoryModal"'), false, "btnUsageHistoryModal should no longer sit in top toolbar");

  const panelSlice = eagleHtml.slice(panelStart, listStart);
  assert.ok(panelSlice.includes('id="pillBtnUsage"'), "filterPillsPanel must include pillBtnUsage");
  assert.ok(panelSlice.includes('id="excludeUsedSelect"'), "filterPillsPanel must contain excludeUsedSelect");
  assert.ok(panelSlice.includes('id="btnUsageHistoryModal"'), "filterPillsPanel must contain btnUsageHistoryModal");
  assert.ok(panelSlice.includes('id="dropTagAgeRow"'), "dropdownTag must include tag-age preset row");

  // 3. Card & right-inspector: left-click filters like Eagle; right-click opens menu; no hover tip
  const cardFn = extractFunctionSource(scriptSrc, "renderSingleQuestionCardHtml");
  assert.ok(cardFn.includes("onQuestionTagClick(event,"), "card tag pill must bind left-click filter");
  assert.ok(cardFn.includes("openQuestionTagContextMenu(event,"), "card tag pill must bind right-click tag context menu");
  assert.equal(cardFn.includes("打标于"), false, "card tag pill must NOT render or hover-title timestamps");

  const inspFn = extractFunctionSource(scriptSrc, "renderRightInspector");
  assert.ok(inspFn.includes("onQuestionTagClick(event,"), "inspector tag pill must bind left-click filter");
  assert.ok(inspFn.includes("openQuestionTagContextMenu(event,"), "inspector tag pill must bind right-click tag context menu");
  assert.equal(
    inspFn.includes('title="${escapeHtml(t.name)}"'),
    false,
    "inspector tag pill must not show any hover title"
  );
  assert.equal(inspFn.includes("打标于"), false, "inspector tag pill must NOT include timestamp text on hover");

  const clickFn = extractFunctionSource(scriptSrc, "onQuestionTagClick");
  assert.ok(clickFn.includes("focusQuestionTagOnly"), "left-click must reuse Eagle-style focusQuestionTagOnly filter");

  // 4. Left/right panes are drag-resizable; defaults follow Eagle proportions
  assert.ok(eagleHtml.includes('id="inspectorResizeHandle"'), "inspector must expose a resize handle");
  assert.ok(eagleHtml.includes('id="sidebarResizeHandle"'), "left sidebar must expose a resize handle");
  assert.ok(scriptSrc.includes("function initInspectorResize"), "inspector resize must be initialized");
  assert.ok(scriptSrc.includes("function initSidebarResize"), "sidebar resize must be initialized");
  assert.ok(scriptSrc.includes("eagle_inspector_width_px"), "inspector width must persist");
  assert.ok(scriptSrc.includes("eagle_sidebar_width_px"), "sidebar width must persist");
  assert.match(scriptSrc, /INSPECTOR_WIDTH_DEFAULT = 240/, "right default is the narrow property column");
  assert.match(scriptSrc, /SIDEBAR_WIDTH_DEFAULT = 180/, "left default is the narrow folder column");
  assert.match(eagleHtml, /--inspector-width/, "inspector width uses CSS variable");
  assert.match(eagleHtml, /--sidebar-width/, "sidebar width uses CSS variable");

  // 5. Right-click tag menu itself displays relative + exact timestamp and Eagle actions
  const menuFn = extractFunctionSource(scriptSrc, "openQuestionTagContextMenu");
  assert.ok(menuFn.includes("tagCtxTimestampInfo"), "tag context menu must include timestamp block");
  assert.ok(menuFn.includes("打标于"), "tag context menu must display 打标于 relative timestamp");
  assert.ok(menuFn.includes("按打标时长筛选"), "tag context menu must offer tag-age filter flyout");
  assert.ok(menuFn.includes("按此标签时间排序"), "tag context menu must offer tag-time sort flyout");
  assert.ok(menuFn.includes("removeTagFromQuestion"), "tag context menu must allow removing tag from that card");
});

test("extractQuestionYear, formatTagRelativeAndExactTime, tag-age filtering, and tag-time sorting work accurately", () => {
  const scriptSrc = extractInlineScript(eagleHtml);
  const fns = [
    "extractQuestionYear",
    "formatTagExactDateTime",
    "formatTagRelativeAndExactTime",
    "formatTagSourceLabel",
    "questionTagMatchesAge",
    "questionTargetTagsForSpec",
    "questionMatchesTagAgePreset",
    "questionRelevantTagTimeMs",
  ]
    .map((name) => extractFunctionSource(scriptSrc, name))
    .join("\n;\n");

  const sandbox = {
    filterState: { selectedTagIds: [], folderTagIds: [], tagLogic: "and", tagAgePreset: "" },
  };
  vm.runInNewContext(fns, sandbox);

  // 1. Real year extraction (no more hardcoded 2024)
  assert.equal(sandbox.extractQuestionYear({ source_filename: "2025海淀期中.pdf" }, {}, "2025·海淀期中"), 2025);
  assert.equal(sandbox.extractQuestionYear({ source_filename: "高三2026届模拟.docx" }, {}, ""), 2026);
  assert.equal(sandbox.extractQuestionYear({ source_filename: "基础练习.pdf" }, {}, "真题资料"), 0);

  // 2. Relative & exact tag timestamps
  const nowMs = Date.parse("2026-11-25T20:30:00+08:00");
  const t14d = sandbox.formatTagRelativeAndExactTime("2026-11-11T19:20:00+08:00", nowMs);
  assert.equal(t14d.relative, "14 天前");
  assert.ok(t14d.exact.startsWith("2026-11-11 "));

  const t2y = sandbox.formatTagRelativeAndExactTime("2024-08-18T14:05:00+08:00", nowMs);
  assert.equal(t2y.relative, "2 年 3 个月前");
  assert.ok(t2y.exact.startsWith("2024-08-18 "));

  const tEmpty = sandbox.formatTagRelativeAndExactTime("", nowMs);
  assert.equal(tEmpty.relative, "打标时间未记录");
  assert.equal(tEmpty.ms, 0);

  // 3. Tag-age filtering targets active selected tag when present
  const qOldHard = {
    id: "q1",
    tags: [
      { id: "tag-hard", name: "不会", createdAt: "2024-09-01T10:00:00+08:00", needsReview: false },
      { id: "tag-deriv", name: "导数", createdAt: "2026-11-24T10:00:00+08:00", needsReview: false },
    ],
  };
  const qNewHard = {
    id: "q2",
    tags: [
      { id: "tag-hard", name: "不会", createdAt: "2026-11-20T10:00:00+08:00", needsReview: false },
      { id: "tag-deriv", name: "导数", createdAt: "2024-05-01T10:00:00+08:00", needsReview: false },
    ],
  };

  const specOlder30Hard = { selectedTagIds: ["tag-hard"], tagLogic: "and", tagAgePreset: "older_30d" };
  assert.equal(sandbox.questionMatchesTagAgePreset(qOldHard, specOlder30Hard, nowMs), true);
  assert.equal(sandbox.questionMatchesTagAgePreset(qNewHard, specOlder30Hard, nowMs), false);

  const specWithin7Hard = { selectedTagIds: ["tag-hard"], tagLogic: "and", tagAgePreset: "within_7d" };
  assert.equal(sandbox.questionMatchesTagAgePreset(qOldHard, specWithin7Hard, nowMs), false);
  assert.equal(sandbox.questionMatchesTagAgePreset(qNewHard, specWithin7Hard, nowMs), true);

  // 4. Tag-time sort targets active filtered tag ("不会"), ignoring newer/older timestamps of other tags
  const tsOld = sandbox.questionRelevantTagTimeMs(qOldHard, "tagged_asc", { selectedTagIds: ["tag-hard"] });
  const tsNew = sandbox.questionRelevantTagTimeMs(qNewHard, "tagged_asc", { selectedTagIds: ["tag-hard"] });
  assert.ok(tsOld < tsNew, "2-year-old '不会' must sort before 5-day-old '不会' even though qOldHard has a newer '导数' tag");
});
