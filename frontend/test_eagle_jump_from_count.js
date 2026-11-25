"use strict";

// 顶栏不再常驻「共 N 题」：左侧文件夹数字已经够用；跳转入口也不再挂在总数上。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "eagle", "index.html"), "utf8");

assert.doesNotMatch(html, /id="btnMatchedCountJump"/);
assert.doesNotMatch(html, /id="jumpQuestionPopover"/);
assert.doesNotMatch(html, /id="eaglePositionIndicator"/);
assert.doesNotMatch(html, /当前第/);
assert.doesNotMatch(html, />\s*共\s*<strong id="matchedQuestionCount"/);
assert.match(html, /id="matchedQuestionCount"/);
assert.match(html, /function jumpToQuestionOrder\(/);
assert.match(html, /命中总数只写在左侧文件夹数字里/);

console.log("Eagle 顶栏去掉「共 N 题」：OK");
