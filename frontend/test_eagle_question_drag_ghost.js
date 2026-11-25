"use strict";
// 题目拖入文件夹：自定义角标幽灵，不再用浏览器默认整卡半透明。
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

function fnBody(name) {
  const lines = html.split(/\r?\n/);
  const start = lines.findIndex((l) =>
    new RegExp(`^\\s*(async )?function ${name}\\(`).test(l)
  );
  assert.ok(start >= 0, `应能找到函数 ${name}`);
  const indent = lines[start].match(/^\s*/)[0].length;
  let end = -1;
  for (let i = start + 1; i < lines.length; i += 1) {
    if (new RegExp(`^\\s{${indent}}\\}\\s*$`).test(lines[i])) {
      end = i;
      break;
    }
  }
  assert.ok(end > start, `应能找到函数 ${name} 的结束`);
  return lines.slice(start, end + 1).join("\n");
}

test("题目拖拽角标视觉", async (t) => {
  await t.test("1. 样式与钩子就位", () => {
    assert.match(html, /\.eagle-q-drag-ghost\s*\{/);
    assert.match(html, /\.ftree-drop-receive\s*\{/);
    assert.match(html, /@keyframes ftreeReceivePulse/);
    assert.match(html, /ondragend="handleQuestionDragEnd\(event\)"/);
    assert.match(html, /function buildQuestionDragGhost\(/);
    assert.match(html, /setDragImage\(ghost/);
    assert.match(html, /ftree-drop-receive/);
  });

  await t.test("2. 角标内容：单题文案 / 多题徽章", () => {
    const appended = [];
    const context = {
      questionsData: [
        { id: "q1", number: "3", stem: "已知函数" },
        { id: "q2", number: "4" },
      ],
      selectedQuestionIds: new Set(),
      getQuestionDisplayName(q) {
        return "第" + q.number + "题";
      },
      questionIdentity(q) {
        return q.number;
      },
      document: {
        createElement(tag) {
          const el = {
            tagName: String(tag).toUpperCase(),
            className: "",
            style: {},
            children: [],
            _attrs: {},
            setAttribute(k, v) {
              this._attrs[k] = v;
            },
            querySelector(sel) {
              if (sel === ".eqd-label") return this._label;
              if (sel === ".eqd-sub") return this._sub;
              return null;
            },
            set innerHTML(htmlStr) {
              this._html = htmlStr;
              this._label = { textContent: "" };
              this._sub = { textContent: "" };
            },
            get innerHTML() {
              return this._html || "";
            },
          };
          return el;
        },
        body: {
          appendChild(el) {
            appended.push(el);
          },
        },
        getElementById() {
          return null;
        },
      },
      window: {},
      Array,
      String,
      Number,
      Math,
      JSON,
      Map,
      Set,
    };
    vm.runInNewContext(
      [
        fnBody("clearQuestionDragGhost"),
        fnBody("buildQuestionDragGhost"),
      ].join("\n\n"),
      context
    );

    const one = context.buildQuestionDragGhost(["q1"]);
    assert.equal(appended.length, 1);
    assert.equal(one.className, "eagle-q-drag-ghost");
    assert.equal(one.querySelector(".eqd-label").textContent, "第3题");
    assert.equal(one.querySelector(".eqd-sub").textContent, "拖到左侧文件夹归档");
    assert.doesNotMatch(one.innerHTML, /eqd-badge/);

    const many = context.buildQuestionDragGhost(["q1", "q2", "q3"]);
    assert.match(many.innerHTML, /eqd-badge/);
    assert.match(many.innerHTML, />3</);
    assert.equal(many.querySelector(".eqd-sub").textContent, "已选 3 道 · 拖到文件夹");
  });
});
