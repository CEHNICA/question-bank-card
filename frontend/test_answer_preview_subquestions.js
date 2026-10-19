"use strict";
// 1.25.38：答案分成 (1)(2)(3) 小问显示时，预览也要带 data-qb-field="answer"，
// 并把每一问的源偏移交给跟踪层。原来只有「没有小问」那条分支带标记，于是答案
// 里有小问时编辑器找不到答案区，还多画一行「答案：（当前为空）」。
const assert = require("node:assert/strict");
const S = require("./library-solutions.js");

function element(tag, className = "", text) {
  return {
    tagName: tag, className, textContent: text || "", children: [], dataset: {},
    append(...values) { this.children.push(...values); },
    replaceChildren(...values) { this.children = [...values]; },
  };
}
const all = (root) => [root, ...root.children.filter(item => item && typeof item === "object").flatMap(all)];

const calls = [];
const QB = {
  subQuestionParts: () => [
    { label: "(1)", lead: "答：", offset: 4, body: "甲" },
    { label: "(2)", lead: "", offset: 12, body: "乙" },
  ],
  renderTypeset: (target, value, options) => { calls.push({ target, value, options }); },
};

const tracked = element("div");
S.render(tracked, { answer: "(1) 甲 (2) 乙", analysis: "" }, { node: element, QB, subQuestions: true, trackSource: true });
const list = all(tracked).find(item => item.className === "qb-subquestions");
assert.ok(list, "答案分成小问显示时仍然有小问容器");
assert.equal(list.dataset.qbField, "answer", "小问容器就是答案区，光标才找得到");
assert.deepEqual(calls.map(call => call.options?.sourceOffset), [0, 4, 12],
  "每问的源偏移按它在整段答案里的位置交给渲染层（不含小问的前缀从 0 开始）");

const untracked = element("div");
calls.length = 0;
S.render(untracked, { answer: "(1) 甲 (2) 乙", analysis: "" }, { node: element, QB, subQuestions: true });
assert.ok(calls.length >= 2 && calls.every(call => call.options === undefined),
  "出卷预览不跟踪光标，也不带数据标记");
const plainList = all(untracked).find(item => item.className === "qb-subquestions");
assert.equal(plainList.dataset.qbField, undefined);

console.log("Answer preview: sub-question answers keep their field and source offsets: OK");
