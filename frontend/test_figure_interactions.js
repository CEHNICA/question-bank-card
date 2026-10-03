"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// 配图归属不再是会跨题残留的常驻选择框；只有点候选图或画完新框后才显示就地菜单。
assert.doesNotMatch(html, /id="slotField"|id="slotSelect"|新框属于/);
assert.match(html, /id="figureSlotMenu"[^>]*popover="manual"/);
for (const slot of ["stem", "A", "B", "C", "D", "irrelevant"]) {
  assert.match(html, new RegExp(`data-figure-slot="${slot}"`));
}
assert.match(js, /openFigureSlotMenu\(option,[\s\S]*kind:\s*"new"/);
assert.match(js, /openFigureSlotMenu\(preview,[\s\S]*kind:\s*"new"/);
assert.match(js, /dialog\.pendingFigure\s*\|\|\s*dialog\.slotTarget\?\.kind\s*===\s*"new"/);
assert.doesNotMatch(js, /const order = \["stem", \.\.\.OPTION_KEYS\]/);

// “无关”不作为后端 slot 发送：候选图会被忽略，已有框会被删除。
assert.match(js, /slot === "irrelevant"[\s\S]*ignoredCandidates\.add/);
assert.match(js, /slot === "irrelevant"[\s\S]*dialog\.boxes\.splice/);
assert.match(js, /isKnownFigureCandidate\(box\) \? figureCandidateKey\(box\) : null/);
assert.match(js, /const candidateKey = box\.candidate_key[\s\S]*dialog\.ignoredCandidates\.add\(candidateKey\)/);
assert.match(js, /candidate_key: target\.candidateKey/);
assert.match(js, /candidate_key: box\.candidate_key/);
assert.match(js, /const used = dialog\.boxes\.some\(\(box\) => box\.candidate_key === candidateKey/);
assert.match(js, /ignored_candidates\)\s*\? q\.figure_review\.ignored_candidates\.filter\(\(key\) => hasFigureCandidateKey\(q, key\)\)/);
assert.match(js, /slot === "irrelevant"[\s\S]*dialog\.boxes\.splice[\s\S]*renderPageTabs\(\)/);
assert.match(js, /const figures = figuresFromBoxes\(dialog\.boxes\)/);
assert.match(js, /confirmCurrentFigures[\s\S]*ignored_candidates: \[\.\.\.ignoredCandidates\]/);
assert.match(js, /slot: figure\.slot \|\| "stem"/);

// 标签可单独拖动；偏移随保存负载发送，而框本身的 bbox 不会在标签拖动函数里被改写。
const labelDrag = js.match(/function startLabelDrag[\s\S]*?\r?\n  }\r?\n\r?\n  \$\("figureSlotMenu"\)/)?.[0] || "";
assert.match(labelDrag, /box\.label_offset\s*=/);
assert.doesNotMatch(labelDrag, /box\.bbox\s*=/);
assert.match(js, /label_offset:\s*\{ \.\.\.box\.label_offset \}/);
assert.match(js, /function autoPlaceFigureLabels/);
assert.match(js, /const manualTabs = tabs\.filter[\s\S]*const automaticTabs = tabs\.filter[\s\S]*manualTabs\.forEach[\s\S]*automaticTabs\.forEach/);
assert.match(js, /function clampFigureLabel[\s\S]*rect\.right > surfaceRect\.right/);
assert.match(js, /automaticTabs\.forEach[\s\S]*clampFigureLabel\(tab, offset, surfaceRect\)/);
assert.match(js, /autoPlaceFigureLabels[\s\S]*positionFigureSlotMenu\(dialog\.slotAnchor\)/);
assert.match(css, /\.edit-box\.figure:not\(\.selected\) \.box-remove\s*\{\s*display:\s*none/);

// 菜单在最上层，并会根据视口空间向上翻转，窄窗口仍可滚到最后一项。
assert.match(js, /const opensUp = below \+ menuRect\.height > viewportHeight/);
assert.match(js, /window\.addEventListener\("resize"[\s\S]*positionFigureSlotMenu\(dialog\.slotAnchor\)/);
assert.match(js, /window\.addEventListener\("resize"[\s\S]*autoPlaceFigureLabels\(surface\)/);
assert.match(css, /\.figure-slot-menu\s*\{[^}]*position:\s*fixed[^}]*max-height:[^}]*overflow:\s*auto/s);
assert.match(css, /z-index:\s*2147483647/);

// 配图冲突不再留下灰色死按钮；操作按冲突信号分流，未分类候选不能误走“确认无图”。
assert.match(js, /"处理配图冲突"/);
// A card whose figures block approval offers the way to fix them where the
// (removed) approve button used to be; approving itself is the tick's job.
assert.match(js, /if \(!approved && figureBlocksApproval\(q\)\) \{[\s\S]*?button\(blockedLabel, "primary", \(\) => focusFigureReview\(q\)/);
for (const action of ["张候选图", "题目范围切多了 · 调整范围", "当前配图正确，其余"]) {
  assert.match(js, new RegExp(action));
}
assert.match(js, /signals\.has\("candidate_unclassified"\)/);
assert.doesNotMatch(js, /slot:\s*"stem"[\s\S]{0,200}ignored_candidates/);
assert.match(js, /if \(figureBlocksApproval\(q\)\) \{ focusFigureReview\(q\); return; \}/);

// 取消 is a quiet button beside the title (like Esc), not a full-width block under the hint.
assert.match(html, /<div class="figure-slot-head">\s*<strong>这张图属于<\/strong>\s*<button type="button" class="figure-slot-cancel" data-figure-slot-cancel[^>]*>取消<kbd>Esc<\/kbd><\/button>/);
assert.match(css, /\.figure-slot-options button\.irrelevant \{ grid-column: 1 \/ -1;/);
assert.doesNotMatch(css, /\.figure-slot-cancel \{ width: 100%/);

console.log("figure interaction regression checks: OK");
