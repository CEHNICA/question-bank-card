"use strict";

// A question's tags must be editable in exactly one place. The panel used to offer a
// second set of accept/reject buttons next to the picker, so the same decision could be
// made twice and the two surfaces could disagree. These checks pin the merged shape:
// one dialog holding what the question already has, what AI proposed, and the directory.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const libraryJs = fs.readFileSync(require.resolve("./library.js"), "utf8");
const libraryCss = fs.readFileSync(require.resolve("./library.css"), "utf8");

// ---- the dialog owns all three blocks
assert.match(libraryJs, /const now = node\("div", "tag-picker-now"\); now\.id = "tagPickerNow";/,
  "the tag dialog reserves one container for the question's own tags");
assert.match(libraryJs, /tagDialog\.append\(header, now, more, search, quick, body, footer\)/,
  "that container sits above the directory list, so it is the first thing read");
assert.match(libraryJs, /moreTitle\.append\(node\("b", "", "想加的"\)/,
  "the directory below is labelled too, so all three blocks read the same way");
// 1.25.94：新建标签的入口从右上角那个孤零零的按钮，挪到了列表最后一条
// （「新建「XXX」并贴上」）—— 跟单题那边同款，不再是两套做法。
assert.doesNotMatch(libraryJs, /tagPickerCreateTag/,
  "the separate create button is gone — creating belongs with the list it adds to");
assert.match(libraryJs, /新建「\$\{trimmed\.slice\(0, 30\)\}」并贴上/,
  "creating a new label is the last row of the list, worded like the single-question one");
// 窗口现在只管「改标签」。采纳/拒绝搬到了右边详情面板的建议清单里（那块下面
// 本来就有空位，核对是最高频的动作，不该藏在一个要点的按钮后面）。
// 所以窗口的说明不能再声称「采纳也在这个窗口里」—— 那会把人引到没有按钮的地方。
assert.match(libraryJs, /要采纳 AI 的建议，在右边详情面板上做。/,
  "the dialog points accepting at the panel instead of claiming it happens here");

const start = libraryJs.indexOf("  function renderTagPickerNow() {");
const end = libraryJs.indexOf("  function renderTagPicker() {", start);
assert.ok(start >= 0 && end > start, "the single-panel renderer is available for behavior checks");
const painter = libraryJs.slice(start, end);
assert.match(painter, /已经在这道题上/, "the first block says what the question already carries");
assert.match(painter, /AI 建议的/, "the second block says what AI proposed");
assert.match(painter, /if \(tagTargetIds\.length !== 1\) \{ box\.hidden = true; return; \}/,
  "a batch tidy has no single question to describe, so the blocks step aside");
assert.match(libraryJs.slice(libraryJs.indexOf("  function renderTagPicker() {"), libraryJs.indexOf("  async function toggleTag(")),
  /renderTagPickerNow\(\);/,
  "every repaint of the dialog redraws the question's own tags, so a removal shows up at once");

// ---- 核对在哪做：面板上就地做完，标签窗口不再摆第二份
// 以前面板只给一个「去核对」的按钮，其余全在窗口里：看建议要点一次，改完名再采纳
// 又要跳回来。面板那块下面本来就是空着的，于是改成直接摊开。
const extrasStart = libraryJs.indexOf("  function extrasNode(item,");
const extrasEnd = libraryJs.indexOf("    Object.entries(item.job_errors || {})", extrasStart);
assert.ok(extrasStart >= 0 && extrasEnd > extrasStart, "the question panel region is available for checks");
const extras = libraryJs.slice(extrasStart, extrasEnd);
assert.match(extras, /item\.tag_suggestion\?\.status === "pending"/,
  "only a proposal that still needs a decision gets the full list");
assert.match(extras, /const panel = tagSuggestionNode\(item, \{ embedded: true \}\);/,
  "the panel lays the suggestions out itself, rename and accept/reject included");
assert.match(extras, /if \(panel\) box\.append\(panel\);\s*else \{/,
  "the way into the tag window is only a fallback for when no row could be drawn");
assert.match(extras, /AI 有 \$\{pendingCount\} 条建议待核对/,
  "the fallback still says how many are waiting, so nothing disappears silently");
// 两个分支互斥：待核对的摊开清单，其余状态只留一行说明。运行时只会走其中一条，
// 所以是「一次一块」而不是「渲染两次」—— 只数出现次数会把正确的互斥写法也判成错。
assert.match(extras, /\} else \{\s*const panel = tagSuggestionNode\(item\); if \(panel\) box\.append\(panel\);/,
  "proposals that are not awaiting a decision keep their plain status line");
assert.equal((extras.match(/box\.append\(panel\)/g) || []).length, 2,
  "each of the two mutually exclusive branches appends exactly one proposal card");
// 窗口里一份都不画：同一份内容出现在两处，看两遍还得对得上两份。
const pickerNowStart = libraryJs.indexOf("  function renderTagPickerNow() {");
const pickerNowEnd = libraryJs.indexOf("  const kindLabels =", pickerNowStart);
const pickerNow = libraryJs.slice(pickerNowStart, pickerNowEnd);
assert.ok(pickerNowStart >= 0 && pickerNowEnd > pickerNowStart, "the tag window renderer is available for checks");
assert.doesNotMatch(pickerNow, /tagSuggestionNode\(/,
  "the tag window must not render a second copy of the proposal card");
assert.match(pickerNow, /"去右边面板核对"/,
  "the window still points at the panel instead of duplicating it");
assert.match(pickerNow, /focusInspectorTag\(item\)/,
  "that button jumps straight to this question's list on the panel");

// ---- behavior: the blocks render from real state
class Element {
  constructor(tag, cls = "", text = "") {
    this.tagName = tag; this.className = cls; this.textContent = text;
    this.children = []; this.hidden = false; this.dataset = {};
    // 1.25.93：要能读到写进 CSS 变量的颜色，才能验「同一颗标签到处是同一个色」。
    const styleValues = {};
    this.style = { values: styleValues, setProperty(name, value) { styleValues[name] = value; } };
    this.listeners = {};
  }
  append(...kids) { this.children.push(...kids); }
  replaceChildren(...kids) { this.children = [...kids]; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  setAttribute() {}
}
function textOf(element) {
  if (typeof element === "string") return element;
  return [element.textContent || "", ...element.children.map(textOf)].join(" ").replace(/\s+/g, " ").trim();
}
function walk(element, matches, out = []) {
  if (!element || typeof element === "string") return out;
  if (matches(element)) out.push(element);
  element.children.forEach(child => walk(child, matches, out));
  return out;
}

const box = new Element("div", "tag-picker-now");
const suggestionCard = new Element("aside", "tag-suggestion-card");
const removed = [];
const context = vm.createContext({
  node: (tag, cls = "", text = "") => new Element(tag, cls, text),
  $: id => (id === "tagPickerNow" ? box : null),
  // 1.25.93：颜色规则搬到了共享的 library-workspace.js（标签管理页在另一个文件里，
  // 也得用同一套），窗口里算颜色也调它 —— 这里把真的那份塞进来，不拿假的凑数。
  workspace: require("./library-workspace.js"),
  state: { catalog: new Map([["pub-1", {
    id: "pub-1",
    tag_suggestion: { id: "sug-1", status: "pending", message: "提示", suggestions: [{}, {}, {}] }
  }]]) },
  tagDialog: { classList: { contains: () => false } },
  tagTargetIds: ["pub-1"],
  tagMemberships: { "pub-1": new Set(["tag-1", "tag-2"]) },
  // 1.25.93：tag-2 故意不给颜色 —— 老师的标签几乎都没设过颜色。以前这种标签在窗口里
  // 就是一颗没颜色的灰标签，现在按类型/学段取一个，同一个标签到处是同一个色。
  tagCatalogue: { tags: [
    { id: "tag-1", name: "平行四边形", color: "#123456", kind: "knowledge", levels: ["初中"], parent_ids: [] },
    { id: "tag-2", name: "等腰三角形", kind: "knowledge", levels: ["初中"], parent_ids: [] },
  ] },
  tagSuggestionNode: () => suggestionCard,
  toggleTag: (tag, remove) => { removed.push([tag.name, remove]); },
});
vm.runInContext(painter, context);
context.renderTagPickerNow();

assert.equal(box.hidden, false, "a single question shows its own block");
assert.match(textOf(box), /已经在这道题上/);
assert.match(textOf(box), /平行四边形/, "the tag already on the question is listed, not just a count");
assert.match(textOf(box), /AI 建议的/, "the block still says AI has suggestions");
assert.match(textOf(box), /3 条待核对/, "the proposal count is shown next to the block");
assert.match(textOf(box), /在右边详情面板上核对/, "the count says where to go, not that it can be done here");
// 同一份建议清单不在窗口里再画一遍：两处内容看两遍，还得对得上两份，改一处另一处就过期。
assert.equal(walk(box, node => node === suggestionCard).length, 0,
  "the window draws no second copy of the proposal card");
const jump = walk(box, node => node.textContent === "去右边面板核对")[0];
assert.ok(jump, "the window offers a way to the panel");
assert.equal(typeof jump.listeners.click[0], "function", "that button is wired to jump");
assert.match(jump.title, /右边详情面板/, "it says where it will take you");

const drop = walk(box, node => node.className === "tag-picker-current-drop")[0];
assert.ok(drop, "each owned tag offers a way to take it off");
assert.match(drop.title, /从这道题移除「平行四边形」/, "the removal control names the tag it removes");
drop.listeners.click[0]();
assert.deepEqual(removed, [["平行四边形", true]], "removing goes through the same save path as the list");

// 1.25.93：同一颗标签，在窗口里、在题卡上、在详情里，颜色得是同一个。
// 这里量的是**真代码画出来的**颜色，不是规则文本：自己设了颜色的用自己那个；
// 没设过的（老师的标签几乎都是这种）跟同学段那颗自己设了色的顶级标签走，
// 而不是一颗没颜色的灰标签。
const ownedChips = walk(box, node => node.className === "tag-picker-current-chip");
assert.equal(ownedChips.length, 2, "两颗标签各画一颗");
assert.deepEqual(ownedChips.map(chip => chip.style.values["--tag-color"]), ["#123456", "#123456"],
  "1.25.93：自己设了颜色的用自己那个，没设的跟同学段那颗走 —— 同一个标签到处是同一个色");
assert.ok(ownedChips.every(chip => /^#[0-9a-f]{6}$/i.test(String(chip.style.values["--tag-color"]))),
  "每一颗都上得了色，不留一颗没颜色的灰标签");

box.replaceChildren();
context.tagTargetIds = ["pub-1", "pub-2"];
context.renderTagPickerNow();
assert.equal(box.hidden, true, "a batch tidy drops the single-question blocks instead of describing one of many");
assert.equal(box.children.length, 0);

// ---- the block has to scroll, or the status bar is pushed off the dialog
assert.match(libraryCss, /\.tag-picker-now \{[^}]*max-height: [^;]+;/,
  "the question's own blocks are capped so they cannot push the status bar away");
assert.match(libraryCss, /\.tag-picker-now \{[^}]*overflow-y: auto;/,
  "long proposal lists scroll inside the dialog");
assert.match(libraryCss, /#tagPickerDialog \.tag-picker-body \{ min-height: [^;]+; \}/,
  "the directory keeps a usable height even when the proposals above it are long");
assert.match(libraryCss, /\.tag-picker-now\[hidden\] \{ display: none; \}/,
  "a hidden block takes no space in batch mode");

console.log("single tag panel checks: OK");