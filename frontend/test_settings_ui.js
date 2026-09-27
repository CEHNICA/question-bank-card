"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// 顶栏只有一个带文字的设置入口，抽屉包含约定的五个分区。
assert.match(html, /id="settingsButton"[^>]*>[\s\S]*?<use href="#i-gear"\/>[\s\S]*?设置<\/button>/);
for (const id of ["settingsModels", "settingsReview", "settingsTask", "settingsInterface", "settingsAbout"]) {
  assert.match(html, new RegExp(`id="${id}"`));
}

// 三个模型角色和保存契约必须保持一致。
for (const id of ["settingsPrimaryModel", "settingsCheckerModel", "settingsArbiterModel"]) {
  assert.match(html, new RegExp(`id="${id}"`));
}
assert.match(js, /api\("\/api\/settings\/models"/);
assert.match(js, /primary:\s*\$\("settingsPrimaryModel"\)\.value/);
assert.match(js, /checker:\s*\$\("settingsCheckerModel"\)\.value/);
assert.match(js, /arbiter:\s*\$\("settingsArbiterModel"\)\.value/);

// 打开后把键盘焦点放到关闭按钮；原生 dialog 的 Esc 与显式关闭按钮都可退出。
assert.match(js, /\$\("settingsDialog"\)\.showModal\(\)/);
assert.match(js, /requestAnimationFrame\(\(\) => \$\("settingsClose"\)\.focus\(\)\)/);
assert.match(html, /id="settingsClose"[^>]*data-close/);

// 窄屏设置必须占满视口，而不是保留桌面抽屉宽度。
assert.match(css, /@media \(max-width: 760px\)[\s\S]*?\.settings-dialog\s*\{[^}]*width:\s*100vw[^}]*border-radius:\s*0/);

// 结构冲突的快捷处理以及新的审核状态文案必须保留。
assert.match(js, /\/api\/papers\/\$\{splitPlan\.paperId\}\/split/);
assert.match(html, /id="settingsConfirmStructure"/);
assert.match(js, /\/api\/papers\/\$\{paper\.id\}\/confirm-structure/);
assert.match(js, /engines\.saved\?\.\[role\]\s*\|\|\s*engines\.selected/);
assert.match(js, /AI 两次一致 · 未人工审核/);
assert.match(js, /AI 三读多数一致 · 未人工审核/);

// 同一页存在多个题号作用域时，补录必须显式选择题组并把 group_id 交给后端。
assert.match(html, /id="groupField"[\s\S]*?id="groupSelect"/);
assert.match(js, /state\.paper\.question_groups/);
assert.match(js, /body\.group_id\s*=\s*Number\(selectedGroup\)/);

// 真正的旧任务迁移后默认仍是“试卷”；失败的 PDF 必须给人明确的教材重试入口。
assert.match(js, /按教材重试/);
assert.match(js, /body:\s*materialType\s*\?\s*\{\s*material_type:\s*materialType\s*\}\s*:\s*\{\}/);

console.log("settings UI static checks: OK");
