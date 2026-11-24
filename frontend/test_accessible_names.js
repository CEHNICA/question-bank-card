"use strict";
// 可访问名与键盘焦点可见性的守护测试（第九轮）。
//
// 实测发现的真缺陷（Playwright + DOM 几何 + 命中测试，不是审美问题）：
//
//   题库工作台（frontend/eagle/index.html）
//     题卡勾选框（卡片模式 / 紧凑模式各一处）
//         只有一截 12px 的方框，屏幕阅读器只能读 "checkbox"，
//         509 张卡里根本分不清是哪一题。补 aria-label="选择题目 <题号/出处>"。
//     19 处表单控件只有 placeholder
//         placeholder 是最弱的可访问名（输入后就消失），
//         搜索框 / 题目名称 / 注释 / 链接 / 题型 / 题干 / 选项 / 答案 / 分值
//         这些都补了 aria-label。
//     导出面板 7 个 <label> 没有 for=
//         视觉上写着「试卷标题」「输出内容」「分页」……但程序上跟控件没关系，
//         点标签不聚焦，读屏也不认。补 for="<控件 id>"。
//     右键菜单搜索框只有 placeholder -> 补 aria-label。
//
//   经典题库页（frontend/styles.css）
//     .qtype-select:focus-visible 的 outline: none
//         题型是下拉，不是按钮，不换焦点态的边框和底色，
//         键盘用户完全看不出焦点在哪。补一条独立焦点环规则。
//
// 这个测试是**静态源码断言**：喂修复前的 HTML/CSS（--html= / --css=）进来必须变红。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { it } = require("node:test");

function arg(name) {
  const hit = process.argv.find((a) => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : null;
}

const eaglePath = arg("html") || path.join(__dirname, "eagle", "index.html");
const cssPath = arg("css") || path.join(__dirname, "styles.css");
const eagle = fs.readFileSync(eaglePath, "utf8");
const css = fs.readFileSync(cssPath, "utf8");

// ---------------------------------------------------------------- 轻量 HTML 扫描

/** 把 HTML 切成开/闭标签序列，够算"控件是否被 <label> 包裹"。 */
function scanTags(html) {
  const out = [];
  const lower = html.toLowerCase();
  const re = /<\/?([a-zA-Z][\w-]*)\b([^>]*)>/g;
  let m;
  while ((m = re.exec(html))) {
    const closing = m[0].startsWith("</");
    const name = m[1].toLowerCase();
    if (name === "script" || name === "style") {
      // 闭合标签要先判，否则 </script> 会被当成又一次开标签，把后面全吞掉。
      if (closing) continue;
      const end = lower.indexOf(`</${name}`, re.lastIndex);
      if (end >= 0) re.lastIndex = end;
      continue;
    }
    const selfClosing = /\/>$/.test(m[0]) || VOID_TAGS.has(name);
    out.push({ closing, selfClosing, name, attrs: m[2] || "" });
  }
  return out;
}

const VOID_TAGS = new Set([
  "area", "base", "br", "col", "embed", "hr", "img", "input",
  "link", "meta", "param", "source", "track", "wbr",
]);

function attr(attrs, key) {
  const re = new RegExp(`\\b${key}\\s*=\\s*"([^"]*)"`);
  const hit = attrs.match(re);
  return hit ? hit[1] : null;
}

/** 顶层 CSS 规则 [{ selectors, body, index }]（本项目 CSS 无嵌套）。 */
function cssRules(text) {
  // 先去注释，否则注释里的 { } 和选择器文字会污染规则切分
  const clean = text.replace(/\/\*[\s\S]*?\*\//g, "");
  const out = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m;
  while ((m = re.exec(clean))) {
    out.push({
      selectors: m[1].trim(),
      body: m[2],
      index: m.index,
    });
  }
  return out;
}

/**
 * 一趟扫出每个表单控件的：可访问名 + 是否被 <label> 包裹 + 是否落在隐藏容器里。
 * 隐藏判定要算祖先（<div class="hidden" aria-hidden="true"> 里的控件对读屏是不存在的）。
 */
function scanControls(html) {
  const fors = new Set();
  const labels = [];
  for (const t of scanTags(html)) {
    if (t.name === "label" && !t.closing) {
      const f = attr(t.attrs, "for");
      if (f) fors.add(f);
      labels.push(t);
    }
  }

  const out = [];
  const stack = [];
  for (const t of scanTags(html)) {
    if (t.closing) {
      for (let i = stack.length - 1; i >= 0; i--) {
        if (stack[i].name === t.name) {
          stack.length = i;
          break;
        }
      }
      continue;
    }
    const inLabel = stack.some((s) => s.name === "label");
    const hiddenByAncestor = stack.some(
      (s) =>
        /(^|\s)hidden(\s|$)/.test(s.attrs) ||
        attr(s.attrs, "aria-hidden") === "true"
    );
    if (/^(input|select|textarea)$/.test(t.name)) {
      out.push({
        tag: t.name,
        id: attr(t.attrs, "id"),
        attrs: t.attrs,
        inLabel,
        hiddenByAncestor,
      });
    }
    if (!t.selfClosing) stack.push(t);
  }
  return { controls: out, fors };
}

/** 控件的可访问名（名字优先级同浏览器：aria-label > aria-labelledby > 关联/包裹 label > 文本 > title > placeholder）。 */
function controlNames(html) {
  const { controls, fors } = scanControls(html);
  const found = [];
  for (const c of controls) {
    const attrs = c.attrs;
    const id = c.id;
    const ariaLabel = attr(attrs, "aria-label");
    const ariaLabelledby = attr(attrs, "aria-labelledby");
    const title = attr(attrs, "title");
    const placeholder = attr(attrs, "placeholder");
    const hidden =
      c.hiddenByAncestor ||
      /\bhidden\b/.test(attrs) ||
      attr(attrs, "aria-hidden") === "true";
    let name = null;
    let via = null;
    if (ariaLabel && ariaLabel.trim()) {
      name = ariaLabel.trim();
      via = "aria-label";
    } else if (ariaLabelledby && ariaLabelledby.trim()) {
      name = ariaLabelledby.trim();
      via = "aria-labelledby";
    } else if (id && fors.has(id)) {
      name = id;
      via = "label[for]";
    } else if (id && c.inLabel) {
      name = id;
      via = "wrapped label";
    } else if (title && title.trim()) {
      name = title.trim();
      via = "title";
    } else if (placeholder && placeholder.trim()) {
      name = placeholder.trim();
      via = "placeholder";
    }
    found.push({ tag: c.tag, id, name, via, hidden });
  }
  return found;
}

// ------------------------------------------------------------- 题库工作台

it("Eagle：题卡勾选框必须有可访问名（不能只有一截方框）", () => {
  // 两处：卡片模式 + 紧凑模式
  const checkboxes = [...eagle.matchAll(/<input[^>]*type="checkbox"[^>]*>/gi)].map(
    (m) => m[0]
  );
  assert.ok(checkboxes.length > 0, "没找到任何 checkbox，页面结构变了？");
  // 题卡里的勾选框：带 questionIdentity 的那两个
  const cardBoxes = checkboxes.filter((c) => /questionIdentity\(/.test(c));
  assert.ok(
    cardBoxes.length >= 2,
    `题卡勾选框应至少有卡片/紧凑两处，实际 ${cardBoxes.length} 处`
  );
  for (const box of cardBoxes) {
    assert.match(
      box,
      /aria-label="选择题目 /,
      `题卡勾选框没有 aria-label：${box.slice(0, 160)}`
    );
  }
});

it("Eagle：表单控件不能只靠 placeholder 当名字", () => {
  // 这些控件的 placeholder 是输入提示，不是名字
  const mustHave = [
    "treeSearchInput",
    "globalSearchInput",
    "dropTagSearchInput",
    "inspectorNameInput",
    "inspectorNoteInput",
    "inspectorUrlInput",
    "tagAutocompleteInput",
    "modalSearchTagInput",
    "contextMenuSearchInput",
  ];
  for (const id of mustHave) {
    const tag = [...eagle.matchAll(/<(?:input|select|textarea)\b[^>]*>/gi)].find(
      (m) => new RegExp(`\\bid="${id}"`).test(m[0])
    );
    assert.ok(tag, `控件 #${id} 不见了，页面结构变了？`);
    assert.match(
      tag[0],
      /aria-label="[^"]+"/,
      `#${id} 只有 placeholder，没有 aria-label：${tag[0].slice(0, 160)}`
    );
  }
  assert.ok(!eagle.includes('id="editQuestionModal"'), "旧改题弹窗应已整合移除");
  assert.ok(!eagle.includes('id="solutionEditorModal"'), "旧独立解析弹窗应已整合移除");

  const unifiedJs = fs.readFileSync(path.join(__dirname, "library-unified-editor.js"), "utf8");
  for (const label of ["题型", "正文形式", "题干", "答案", "解析", "解析图来源页", "选择原卷页码"]) {
    assert.ok(
      unifiedJs.includes(`setAttribute("aria-label", "${label}")`),
      `统一编辑工作台控件缺少 aria-label="${label}"`
    );
  }
});

it("Eagle：导出面板的 <label> 必须用 for= 关联控件", () => {
  const pairs = [
    ["试卷标题", "paperMainTitle"],
    ["输出内容", "exportDocument"],
    ["答案解析位置", "exportAnswerLayout"],
    ["分页", "exportPagination"],
    ["选择题选项", "exportOptionLayout"],
    ["正文字号", "exportFontSize"],
    ["解答题留白", "exportAnswerSpace"],
  ];
  for (const [text, id] of pairs) {
    const labelRe = new RegExp(`<label\\b[^>]*>\\s*${text}\\s*</label>`);
    const hit = eagle.match(labelRe);
    assert.ok(hit, `找不到 label「${text}」`);
    assert.match(
      hit[0],
      new RegExp(`for="${id}"`),
      `label「${text}」没有 for="${id}" —— 看着有标签，读屏认不出来：${hit[0]}`
    );
  }
});

it("Eagle：所有可见表单控件都要有名字（无障碍体检的静态版）", () => {
  const names = controlNames(eagle);
  const bad = names.filter(
    (c) => !c.hidden && c.via !== "aria-label" && c.via !== "aria-labelledby" &&
      c.via !== "label[for]" && c.via !== "wrapped label"
  );
  assert.deepEqual(
    bad.map((b) => `${b.tag}#${b.id || "?"}`),
    [],
    "这些控件没有可访问名："
  );
});

it("Eagle：控件不能只剩 placeholder 当名字（弱名字）", () => {
  const names = controlNames(eagle);
  const weak = names.filter(
    (c) => !c.hidden && c.via === "placeholder" && c.tag !== "input"
  );
  // input 的 placeholder 在没有 label 时确实是唯一名字，单独处理
  const weakInput = names.filter(
    (c) => !c.hidden && c.via === "placeholder" && c.tag === "input" &&
      !/^(contextMenuSearchInput|solutionEditorSearchInput)$/.test(c.id || "")
  );
  assert.deepEqual(
    [...weak, ...weakInput].map((b) => `${b.tag}#${b.id || "?"}`),
    [],
    "这些控件只有 placeholder 当名字："
  );
});

// ------------------------------------------------------------- 经典题库页

it("经典页：.qtype-select 焦点时必须有可见焦点环", () => {
  // 要找的是**单独**给 :focus-visible 的规则，不是和 :hover 合并的那条。
  const rules = cssRules(css);
  const focusRules = rules.filter(
    (r) =>
      r.selectors
        .split(",")
        .map((s) => s.trim())
        .some((s) => s === ".qtype-select:focus-visible")
  );
  assert.ok(focusRules.length > 0, "找不到独立的 .qtype-select:focus-visible 规则");
  const withOutline = focusRules.filter((r) => /outline\s*:/.test(r.body));
  assert.ok(
    withOutline.length > 0,
    "独立的 :focus-visible 规则里没有 outline —— 焦点不可见"
  );
  const decl = withOutline[withOutline.length - 1].body.match(
    /outline\s*:\s*([^;]+)/
  )[1].trim();
  assert.notEqual(decl, "none", `焦点环被关掉了：outline: ${decl}`);
  assert.match(
    decl,
    /solid\s+(var\(--accent\)|#[0-9a-f]{3,6})/i,
    `焦点环没有实线颜色：outline: ${decl}`
  );
  assert.match(decl, /(\d+)px/, `焦点环宽度缺失：outline: ${decl}`);
});

it("经典页：焦点环规则必须排在 .undecided 之后（否则被同权重盖掉）", () => {
  const rules = cssRules(css);
  const undecided = rules.find((r) =>
    r.selectors.split(",").some((s) => s.trim() === ".qtype-select.undecided")
  );
  const focus = rules.find(
    (r) =>
      r.selectors.split(",").some((s) => s.trim() === ".qtype-select:focus-visible") &&
      /outline\s*:/.test(r.body)
  );
  assert.ok(undecided, "找不到 .qtype-select.undecided");
  assert.ok(focus, "找不到带 outline 的 .qtype-select:focus-visible");
  assert.ok(
    focus.index > undecided.index,
    "焦点规则在 .undecided 之前 —— 同为 0-2-0 权重时源序在后者胜，焦点环会被盖掉"
  );
});

it("经典页：hover 规则不该再自带 outline: none", () => {
  const rules = cssRules(css);
  const hover = rules.find((r) =>
    r.selectors.split(",").some((s) => s.trim() === ".qtype-select:hover")
  );
  assert.ok(hover, "找不到 .qtype-select:hover 规则");
  assert.ok(
    !/outline\s*:\s*none/.test(hover.body),
    `hover 规则里还留着 outline: none，会连带压掉焦点环：${hover.body}`
  );
});
