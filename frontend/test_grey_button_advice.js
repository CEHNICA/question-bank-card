"use strict";
// 1.26.37：面板里灰掉的按钮必须说清「为什么灰」和「下一步去哪」。
//
// 老师看到「前移」「后移」是灰的，只知道按钮点不动 —— 不知道是自己操作不对、
// 还是压根没得挪、还是软件坏了。这个测试直接跑真模块里的真函数，
// 检查三件事：
//   1) 话术里必须出现「为什么」和「怎么办」，不能只说做什么；
//   2) 不同状态下说的话必须不一样（不能所有情况都同一句）；
//   3) 同一个状态下，调出来的必须是同一个值（说话不许飘）。
const assert = require("node:assert/strict");
const L = require("./original-paper-layout.js");

// ---- 前移 / 后移 ----------------------------------------------------------
// 一段：两个方向都灰
const onePart = L.partOrderHint(-1, 0, 1);
const onePartBack = L.partOrderHint(1, 0, 1);
assert.ok(onePart.length > 0, "一段时也要有话说");
assert.match(onePart, /只有一段|没有先后/, "一段时说清是只有一段，不是坏了");
assert.match(onePart, /再加一块/, "一段时要说下一步做什么");
assert.equal(onePart, onePartBack, "一段时两个方向说的是同一件事（都缺第二段）");

// 第一段：前移灰，后移不灰
const firstUp = L.partOrderHint(-1, 0, 3);
const firstDown = L.partOrderHint(1, 0, 3);
assert.notEqual(firstUp, firstDown, "第一段的前移和后移必须给不同的话");
assert.match(firstUp, /第一段|往前/, "第一段前移灰时说清是第一段");
assert.match(firstDown, /挪|顺序/, "第一段后移可用时说清做什么");

// 最后一段：后移灰
const lastDown = L.partOrderHint(1, 2, 3);
const lastUp = L.partOrderHint(-1, 2, 3);
assert.notEqual(lastUp, lastDown, "最后一段的后移和前移必须给不同的话");
assert.match(lastDown, /最后一段|往后/, "最后一段后移灰时说清是最后一段");

// 中间那段：两个方向都可用
const midUp = L.partOrderHint(-1, 1, 3);
const midDown = L.partOrderHint(1, 1, 3);
assert.notEqual(midUp, midDown, "可用时也要分清往前还是往后");

// 同一状态、同一输入 → 同一句话（不许飘）
assert.equal(L.partOrderHint(-1, 0, 3), firstUp, "同一状态下话术必须稳定");
assert.equal(L.partOrderHint(1, 2, 3), lastDown, "同一状态下话术必须稳定");

// 灰的时候必须给出下一步，不能只说「不能挪」
assert.ok(!/^不能挪[。.]?$/.test(firstUp), "不能只说一句『不能挪』就完事");

// ---- 移除 ----------------------------------------------------------------
const removeOne = L.partRemoveHint(1);
const removeMany = L.partRemoveHint(3);
assert.notEqual(removeOne, removeMany, "一段和多段时移除的说法不同");
assert.match(removeOne, /删除这题/, "只剩一段时要说清整题删掉走哪条路");
assert.match(removeMany, /其它段|找回来|恢复/, "多段时要说清别的段不受影响、能找回来");

// ---- 撤销 / 重做 ----------------------------------------------------------
const undoEmpty = L.historyHint("undo", false, false, false);
const undoReady = L.historyHint("undo", true, false, false);
const redoEmpty = L.historyHint("redo", false, false, false);
const redoReady = L.historyHint("redo", true, false, false);
assert.notEqual(undoEmpty, undoReady, "能不能撤销，说话必须不一样");
assert.notEqual(redoEmpty, redoReady, "能不能重做，说话必须不一样");
assert.notEqual(undoEmpty, redoEmpty, "撤销和重做灰了要说不同的话");
assert.match(undoEmpty, /还没有|先改/, "撤销灰了要说清现在没东西可撤");
assert.match(redoEmpty, /还没有|先改/, "重做灰了要说清现在没东西可重做");
assert.match(undoEmpty, /先改/, "撤销灰了要说下一步做什么");
assert.match(undoReady, /撤/, "能撤的时候说清撤什么");

// 保存中 / 出错：说法必须跟「没东西可撤」不一样
const saving = L.historyHint("undo", false, true, false);
const failed = L.historyHint("undo", false, false, true);
assert.notEqual(saving, undoEmpty, "保存中要说清是在保存");
assert.notEqual(failed, undoEmpty, "读不到状态要说清是读不到");
assert.match(failed, /状态|重开/, "读不到状态时要说下一步");

// ---- 灰按钮的话术必须都有「为什么 + 怎么办」 -----------------------------
// 每一句里至少要有一处说明原因或下一步，不能是光秃秃一个动作名。
for (const [name, text] of [
  ["一段时的前移", onePart],
  ["第一段前移", firstUp],
  ["最后一段后移", lastDown],
  ["中间前移", midUp],
  ["移除一段", removeOne],
  ["移除多段", removeMany],
  ["撤销空", undoEmpty],
  ["撤销可撤", undoReady],
  ["重做空", redoEmpty],
  ["撤销保存中", saving],
  ["撤销读不到", failed],
]) {
  assert.ok(text && text.length >= 10, `${name}：话术不能太短`);
  assert.ok(
    /为什么|因为|已经|没有|还没有|就是|在保存|读|状态|先|再|去|点|用|下|能|才能|改|挪|换|剩/.test(text),
    `${name}：话术里要能看出原因或下一步 —— ${text}`,
  );
}

// 同一句话不许既说「没有」又承诺「能撤」。
assert.ok(
  !(undoEmpty.includes("还没有") && undoEmpty.includes("把刚才那一步改回去")),
  "撤销灰了不能一边说没东西可撤一边说能撤",
);

// ---- 结构检查：话术必须点到真正的那个原因，不能用别的词糊弄过去 ----------
// 这一段不看关键词，只看「说的是不是这一件事」。
assert.match(firstUp, /第一段/, "第一段前移灰：必须说到「第一段」，不能拿别的理由搪塞");
assert.match(lastDown, /最后一段/, "最后一段后移灰：必须说到「最后一段」");
assert.match(onePart, /一段/, "只有一段时：必须说到「一段」");
assert.match(undoEmpty, /还没有|先改/, "撤销灰：必须说到现在没东西可撤");
assert.match(redoEmpty, /还没有|先改/, "重做灰：必须说到现在没东西可重做");
assert.match(saving, /保存/, "保存中：必须说到是在保存");
assert.match(failed, /状态|重开/, "读不到状态：必须说到是状态没读到");

// 能用的时候，话术不许还在讲「为什么灰」—— 那是灰的时候才该说的话。
assert.doesNotMatch(midUp, /没有位置|不能|不可以/, "可用的按钮不许说『不能』");
assert.doesNotMatch(midDown, /没有位置|不能|不可以/, "可用的按钮不许说『不能』");

// 灰的时候必须给出下一步（能做什么），不只是说不能做什么。
for (const [name, text] of [
  ["一段时的前移", onePart],
  ["第一段前移", firstUp],
  ["最后一段后移", lastDown],
  ["撤销空", undoEmpty],
  ["重做空", redoEmpty],
  ["移除一段", removeOne],
]) {
  assert.ok(
    /先|再|去|点|用|改|能|才能|换|挪|恢复|找回来|重开|删/.test(text),
    `${name}：光说不能做什么不够，还得说下一步能做什么 —— ${text}`,
  );
}

console.log("Grey buttons say why they are grey and what to do next: OK");
