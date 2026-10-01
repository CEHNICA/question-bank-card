"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const libraryHtml = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");

// 两个页面和安装版设置使用同一公开品牌，不再露出旧工程名。
for (const page of [html, libraryHtml]) {
  assert.match(page, /<strong>题有据<\/strong>/);
  assert.match(page, /class="brand-mark"[^>]*src="\/favicon\.png"/);
  assert.doesNotMatch(page, /题库题卡版/);
}
assert.match(html, /id="aboutVersion"/);
assert.match(js, /题有据 \$\{s\.app_version\}（本机安装）/);

// 顶栏只有一个带文字的设置入口；设置按分页显示：常用、读题模型、帮助、关于。
assert.match(html, /id="settingsButton"[^>]*>[\s\S]*?<use href="#i-gear"\/>[\s\S]*?设置<\/button>/);
for (const id of ["settingsGeneral", "settingsModels", "settingsReview", "settingsAbout"]) {
  assert.match(html, new RegExp(`data-settings-tab="${id}"`));
  assert.match(html, new RegExp(`id="${id}" class="settings-page" role="tabpanel"`));
}
assert.match(html, /id="settingsInterface"/);
assert.match(js, /function showSettingsTab\(id\)/);
assert.match(js, /showSettingsTab\("settingsGeneral"\);\s*\$\("settingsDialog"\)\.showModal\(\)/);
// “常用”先用一句话说明能不能上传新资料；缺什么密钥就直接说出来。
assert.match(html, /id="settingsReady"/);
assert.match(js, /还不能上传新资料：请先填写 \$\{missing\.join\("、"\)\} 的密钥/);
// 专注和放大镜两个开关在“审核界面”里，与工具栏按钮保持同步。
assert.match(html, /id="settingsFocus" type="checkbox" role="switch"/);
assert.match(js, /if \(\$\("settingsFocus"\)\) \$\("settingsFocus"\)\.checked = on;/);
// 读题模型改了就保存，不再依赖单独的保存按钮。
assert.doesNotMatch(html, /id="settingsModelSave"/);
assert.match(js, /\.forEach\(\(id\) => \$\(id\)\.addEventListener\("change", \(\) => \{ void saveModelSettings\(\); \}\)\)/);
// 当前这份试卷的操作不属于“设置”：在试卷标题旁的“试卷操作”里。
assert.doesNotMatch(html, /id="settingsTask"/);
assert.match(html, /<details class="menu paper-menu" id="paperMenu">[\s\S]*?id="settingsRename"[\s\S]*?id="settingsTaskNotes"[\s\S]*?id="settingsArchive"[\s\S]*?id="settingsDelete"/);

// 三个模型角色和保存契约必须保持一致。
for (const id of ["settingsPrimaryModel", "settingsCheckerModel", "settingsArbiterModel"]) {
  assert.match(html, new RegExp(`id="${id}"`));
}
for (const id of ["settingsMinimaxModel", "settingsSiliconflowModel"]) {
  assert.match(html, new RegExp(`id="${id}"[^>]*list="${id}s"`));
}
assert.match(js, /api\("\/api\/settings\/models"/);
assert.match(js, /primary:\s*\$\("settingsPrimaryModel"\)\.value/);
assert.match(js, /checker:\s*\$\("settingsCheckerModel"\)\.value/);
assert.match(js, /arbiter:\s*\$\("settingsArbiterModel"\)\.value/);
assert.match(js, /models:\s*\{[\s\S]*?minimax:\s*\$\("settingsMinimaxModel"\)\.value\.trim\(\)[\s\S]*?siliconflow:\s*\$\("settingsSiliconflowModel"\)\.value\.trim\(\)/);

// 角色选项与 model_id 建议必须来自后端；建议列表不限制用户填写其他合法 model_id。
assert.match(js, /const choices = settingsEngineChoices\(engines\)/);
assert.match(js, /modelEntries = choices\.map/);
assert.match(js, /engines\.suggested_models\?\.\[providerKey\]/);
assert.match(js, /engines\.saved\?\.models\?\.\[providerKey\]\s*\|\|\s*engines\.models\?\.\[providerKey\]/);
assert.doesNotMatch(js, /MiniMax-M3|Qwen3-VL-32B-Instruct|minimax_m3|siliconflow_qwen3/);
assert.match(js, /API 未配置/);
assert.match(js, /从下一项新任务或重新识读开始生效/);
assert.doesNotMatch(js, /重启桌面程序后生效|重新打开题库后生效/);

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
