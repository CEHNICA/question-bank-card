"use strict";

// Focus: the card being read shows normally, the others dim; the card being
// read follows the scroll.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { readingLine, nearestToLine, mostlyVisible } = require("./app.js");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// 录入页已撤掉「专注」开关与 Z 快捷键（见 test_entry_redesign）；setFocus 只负责清掉旧状态。
assert.doesNotMatch(html, /id="focusToggle"/);
assert.doesNotMatch(js, /case "z":\s*event\.preventDefault\(\);\s*setFocus\(/);
assert.match(js, /function setFocus\(\) \{\s*state\.focus = false;/);
assert.match(js, /function setCurrent\(id, \{ scroll = false, focus = false, hold = scroll \} = \{\}\)/);
assert.match(js, /if \(card && hold\) state\.followHold = true;/);
assert.match(js, /card\.addEventListener\("focusin", \(\) => \{ if \(state\.current !== q\.id\) setCurrent\(q\.id, \{ hold: true \}\); \}\);/);
assert.match(js, /window\.addEventListener\("wheel", releaseFollow, \{ passive: true \}\)/);
assert.match(js, /return \(bar \? bar\.offsetHeight : 56\) \+/);
// 阅读线几何仍给后续全屏/跳题用；只测纯函数，不再要求变暗样式入口。
const band = { top: 100, bottom: 700 };
assert.equal(readingLine({ ...band, scrollY: 2000, below: 2000 }), 300);
assert.equal(readingLine({ ...band, scrollY: 0, below: 2000 }), 100);
assert.equal(readingLine({ ...band, scrollY: 2000, below: 0 }), 699);
assert.equal(readingLine({ ...band, scrollY: 2000, below: 200 }), 500);
// Under the line wins, even when a taller neighbour shows more of itself.
const rects = [{ top: -400, bottom: 280 }, { top: 300, bottom: 520 }, { top: 546, bottom: 1600 }];
assert.equal(nearestToLine(rects, 300), 1);
assert.equal(nearestToLine(rects, 699), 2);
// A line in the gap between cards takes the nearer one.
assert.equal(nearestToLine(rects, 290), 0);
assert.equal(nearestToLine(rects, 535), 2);
assert.equal(nearestToLine([], 300), -1);
// A full card counts only while a fair share of it is on screen.  One slid mostly
// under the toolbar (only its option row and buttons showing) is not being read:
// nothing is lit and nothing is dimmed.
assert.equal(mostlyVisible({ top: -300, bottom: 255 }, 120, 720), false);
assert.equal(mostlyVisible({ top: -100, bottom: 420 }, 120, 720), true);
assert.equal(mostlyVisible({ top: 348, bottom: 897 }, 115, 720), true);    // just expanded, lower half off screen
assert.equal(mostlyVisible({ top: -2000, bottom: 2000 }, 120, 720), true); // taller than the screen
assert.equal(mostlyVisible({ top: 650, bottom: 1300 }, 120, 720), false);  // only its top peeks in
assert.match(js, /const cards = cardNodes\(\)\.filter\(\(card\) => !card\.classList\.contains\("compact"\) && wellInView\(card\)\);/);

console.log("focus mode checks: OK");

// Full-screen review: the top bar, paper list and paper header fold away; the
// toolbar keeps the paper name and progress.  Esc or Q leaves it.
assert.match(html, /id="fullscreenToggle"[^>]*aria-pressed="false"/);
assert.match(css, /:root\.review-fullscreen \{ --topbar-h: 0px; \}/);
assert.match(css, /:root\.review-fullscreen \.topbar, :root\.review-fullscreen \.sidebar, :root\.review-fullscreen \.paper-head,/);
assert.match(js, /root\.requestFullscreen\(\{ navigationUI: "hide" \}\)\.catch\(\(\) => \{\}\)/);
assert.match(js, /document\.addEventListener\("fullscreenchange", \(\) => \{\s*if \(!document\.fullscreenElement\) setReviewFullscreen\(false\);/);
assert.match(js, /case "q":\s*event\.preventDefault\(\);\s*setReviewFullscreen/);
assert.match(js, /const toolbar = \$\("toolbarPaper"\);[\s\S]*?toolbar\.replaceChildren\(el\("strong", "", paperDisplayName/);
// 1.12.7d：点过工具栏的开关之后，Enter 曾经变成「再点一次开关」——最荒唐的是
// 全屏那个，想通过一道题却把屏幕整个铺开了。鼠标点完必须把焦点交回题卡；
// 键盘 Tab 过来激活的（detail 为 0）不抢，Tab 顺序和连按空格切开关都要留着。
assert.match(js, /function releaseToggleFocus\(event\) \{\s*if \(event\.detail === 0\) return;/);
assert.match(js, /\$\("fullscreenToggle"\)\.addEventListener\("click", \(event\) => \{\s*setReviewFullscreen\([\s\S]*?releaseToggleFocus\(event\);/);
assert.doesNotMatch(js, /\$\("focusToggle"\)\.addEventListener/);
assert.match(js, /\$\("lensToggle"\)\.addEventListener\("click", \(event\) => \{ setLens\(!state\.lens\); releaseToggleFocus\(event\); \}\)/);
assert.match(js, /\$\("paperMenu"\)\.addEventListener\("toggle",[\s\S]*?if \(isOpen\) \{ renderSettingsTask\(\); return; \}/);
console.log("full-screen review checks: OK");

// J/K pressed quickly: the card just jumped to may still be scrolling in, so keep
// stepping from it instead of falling back to the top card on screen (it bounced).
assert.match(js, /if \(index >= 0\) next = state\.followHold \|\| onScreen\(cards\[index\]\) \? index \+ step : -1;/);
// A card jumped to lands just under the toolbar: html scroll-padding only, no
// extra scroll-margin on the card (the two used to add up to an 80px gap).
assert.match(css, /html \{ scroll-padding-top: calc\(var\(--topbar-h\) \+ 72px\); \}/);
assert.doesNotMatch(css, /\.card \{[^}]*scroll-margin-top/);
// “原卷截图 · 点击放大对照” is a button that opens the comparison view.
assert.match(js, /const sourceNote = el\("button", "source-note"\);[\s\S]*?sourceNote\.addEventListener\("click", \(\) => openViewer\(q\)\);/);
console.log("keyboard step and zoom caption checks: OK");

// 展开：O 展开 / 收起这张，Shift+O 全部；J/K 跳到收起的已通过题时自动展开、
// 离开时收回（手动展开的不收），设置里可以关，选择记在本机。
assert.match(js, /case "o":\s*event\.preventDefault\(\);\s*if \(event\.shiftKey\) toggleAllExpanded\(\); else toggleExpanded\(q\);/);
assert.match(js, /const nextId = Number\(cards\[next\]\.dataset\.id\);\s*autoExpandOnMove\(nextId\);\s*setCurrent\(nextId, \{ scroll: true, focus: true \}\);/);
assert.match(js, /function autoExpandOnMove\(nextId\) \{[\s\S]*?if \(id === nextId\) return;\s*setExpanded\(id, false\);[\s\S]*?if \(state\.autoExpand && canCollapse\(q\) && !state\.expanded\.has\(nextId\)\) \{\s*setExpanded\(nextId, true, \{ auto: true \}\);/);
assert.match(js, /function canCollapse\(q\) \{\s*return Boolean\(q\) && isApproved\(q\) && !isAiApproved\(q\);/);
assert.match(js, /autoExpand: readPref\("qb-auto-expand", "1"\) === "1"/);
assert.match(js, /writePref\("qb-auto-expand", state\.autoExpand \? "1" : "0"\)/);
assert.match(html, /id="settingsAutoExpand" type="checkbox" role="switch"/);
const focusKeys = require("./shortcut-help.js").reference("review");
assert(focusKeys.more.some(item => item.keys.includes("O") && item.label.includes("展开")));
assert(focusKeys.more.some(item => item.keys.includes("Shift+O") && item.label.includes("全部")));
// A click on 展开 / the row, or E, is a manual expand: it stays open when J/K moves on.
assert.match(js, /setExpanded\(q\.id, collapsed\);/);
assert.match(js, /state\.autoExpanded\.delete\(q\.id\); {4}\/\/ 正在改的题，离开时不收回/);
console.log("expand shortcut checks: OK");
