"use strict";
const assert = require("node:assert/strict");
const S = require("./library-solutions.js");
global.QBRender = require("./qb-render.js"); global.katex = require("./vendor/katex/katex.min.js");
const Export = require("./exam-export.js");
const question = { id: "question", number: 7, content: { stem: "求面积", answer: "A", analysis: "原卷过程", figures: [{ file: "question.png", slot: "stem" }] },
  ai_answer: { answer: "B", analysis: "AI 过程" } };
assert.equal(S.selected(question, { ai_answers: true }).content.answer, "A");
assert.deepEqual(S.selected(question).content.figures, [], "Question diagrams never become solution diagrams");
assert.equal(S.selected({ content: { figures: [{ file: "question.png" }] } }), null, "A diagram-only student question has no answer");
assert.equal(S.completeness({ content: { answer: "A" } }), "result_only");
assert.equal(S.completeness({ content: {}, solution: { figures: [{ id: "image", url: "/api/image" }] } }), "ready", "An image-only detailed answer is usable");
const edited = { ...question, solution: { id: "saved", answer: "C", analysis: "第一步 $x^2$。\n\n第二步。", figures: [{ id: "image", url: "/api/image", display_width: 50, position: "paragraph", paragraph: 0 }] } };
assert.equal(S.selected(edited).content.answer, "C");
const exported = Export.serializeFields([edited], { document: "combined" });
assert.equal(exported.question.answer.source, "C"); assert.equal(exported.question.analysis.source, edited.solution.analysis);
assert.equal(Export.serializeFields([edited], { document: "questions" }).question.answer, undefined, "Student exports exclude saved solutions");
assert.equal(Export.selectedAnswer({ content: {}, solution: { figures: [{ id: "image" }] } }, {}).edited, true);
assert.deepEqual(S.fixedSelections({ question: "saved", removed: "old", bad: null }, ["question", "bad"]), { question: "saved" });
assert.deepEqual(S.draftSelections(undefined, ["question", "second"]), { question: "origin", second: "origin" }, "Legacy drafts retain their source answers even after the library gains new solutions");
assert.deepEqual(S.draftSelections({ question: "saved", removed: "old" }, ["question", "second"]), { question: "saved", second: "origin" });
const legacy = { ...edited, solution_revision: "origin" };
assert.equal(S.selected(legacy).content.answer, "A", "An explicit origin choice wins over a later synced overlay");
assert.equal(Export.serializeFields([legacy], { document: "combined" }).question.answer.source, "A");
assert.deepEqual(S.payload(edited.solution).figures, [{ id: "image", display_width: 50, position: "paragraph", paragraph: 0 }], "Only server asset ids and placement are sent when saving");
assert.equal(question.content.answer, "A", "Editing cannot mutate the source answer");
class Element {
  constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.style = {}; this.dataset = {}; this.className = ""; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}
const node = (tag, className = "", text) => { const value = new Element(tag); value.className = className; if (text) value.append(text); return value; };
const QB = { renderTypeset: (target, value) => target.append(value) };
const host = node("div"); S.render(host, edited.solution, { node, QB });
assert.deepEqual(host.children.map(value => value.className), ["solution-result", "solution-label", "qb-analysis solution-paragraph", "solution-figure", "qb-analysis solution-paragraph"]);
assert.equal(host.children[3].children[0].dataset.solutionWidth, "50");
const onlyImages = node("div"); S.render(onlyImages, { figures: [{ id: "i", url: "/api/i", position: "paragraph", paragraph: 8 }] }, { node, QB });
assert.equal(onlyImages.children[0].className, "solution-figure", "Unavailable paragraph positions safely render after the text without losing the image");
const smallValue = { figures: [{ id: "small", url: "/api/small", display_width: 5, position: "after" }] };
const smallHost = node("div"); S.render(smallHost, smallValue, { node, QB });
assert.equal(smallHost.children[0].children[0].style.width, "5mm", "Small diagrams retain the configured 5 mm size in previews");
assert.equal(smallHost.children[0].children[0].dataset.solutionWidth, "5", "The A4 composer receives the same physical size");
assert.equal(S.payload(smallValue).figures[0].display_width, 5, "Saving/export does not silently enlarge a small diagram");
assert.equal(S.figuresOf({ figures: [{ id: "default" }] })[0].display_width, 20);
assert.equal(S.figuresOf({ figures: [{ id: "rounded", display_width: 164.59199999999998 }] })[0].display_width, 164.59);
assert.equal(S.payload({ figures: [{ id: "rounded", display_width: 164.59199999999998 }] }).figures[0].display_width, 164.59, "UI and saved widths share at most two decimals");
assert.equal(S.jobLabel({ executor: "assistant", status: "queued" }), "保留旧生成任务，未自动重新提交");
assert.equal(S.jobLabel({ executor: "assistant", status: "running" }), "保留旧生成任务，未自动重新提交");
assert.equal(S.jobLabel({ executor: "api", status: "queued" }), "AI 任务排队中");
assert.equal(S.jobLabel({ executor: "api", status: "running" }), "AI 正在解题");
assert.equal(S.jobLabel({ status: "failed", terminal_reason: "cancelled" }), "已取消，勾选可重试");
assert.equal(S.jobLabel({ status: "failed", timed_out: true }), "处理超时，勾选可重试");
assert.equal(S.jobLabel({ status: "done", result: { answer: "B" } }), "初稿已到，保存后出卷");
const aiDraft = { answer: "AI答案", analysis: "AI解析" };
const prefilled = S.editorInitial({ content: {}, ai_answer: aiDraft }, { origin: { answer: "原卷答案", analysis: "" }, ai_answer: aiDraft });
assert.equal(prefilled.value.answer, "原卷答案"); assert.equal(prefilled.value.analysis, "AI解析"); assert.deepEqual(prefilled.ai_fields, ["analysis"]);
assert.notEqual(S.signature(prefilled.value), prefilled.saved, "An AI prefill remains unsaved content");
assert.equal(S.editorInitial({ content: {}, ai_answer: aiDraft }, { origin: {} }).value.answer, "", "Unvalidated catalogue extras never silently prefill the editor");
assert.equal(S.editorInitial({ content: {} }, { origin: {}, ai_answer: aiDraft, ai_answer_stale: true }).value.answer, "", "Stale AI cannot prefill fields");
assert.equal(S.editorInitial({ content: {} }, { solution: { answer: "人工", analysis: "" }, ai_answer: aiDraft }).value.analysis, "", "Manual solutions stay intact, including deliberate empty fields");
assert.deepEqual(S.editorInitial(question, { origin: question.content }).value.figures, [], "Source question figures do not become analysis pictures");
console.log("Saved solution precedence, source protection, image-only answers, student isolation and ordered paragraph figures: OK");
