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

// 两页进入一个常规设置页：服务、AI、显示、帮助、关于。
assert.match(html, /id="settingsButton" href="\/settings">设置<\/a>/);
assert.match(libraryHtml, /href="\/settings">设置<\/a>/);
assert.doesNotMatch(libraryHtml, /data-library-ai-settings/);
for (const id of ["settingsGeneral", "settingsAI", "settingsDisplay", "settingsReview", "settingsAbout"]) {
  assert.match(html, new RegExp(`data-settings-tab="${id}"`));
  assert.match(html, new RegExp(`id="${id}" class="settings-page" role="tabpanel"`));
}
assert.match(html, /id="settingsInterface"/);
assert.match(js, /function showSettingsTab\(id, \{ updateHash = true \} = \{\}\)/);
assert.match(html, /<section id="settingsDialog" class="settings-panel"/);
assert.doesNotMatch(html, /<dialog id="settingsDialog"|id="reopenSettings"/);
assert.match(html, /id="libraryAISettingsMount"/);
assert.match(js, /LibraryAISettings\.mount\(\$\("libraryAISettingsMount"\)\)/);
assert.match(js, /settingsAI: "ai"/);
// Local import is available without cloud credentials.
assert.match(html, /id="settingsReady"/);
assert.match(js, /本地导入、手工切题可直接使用。自动切题还需 \$\{missing\.join\("、"\)\} 密钥/);
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
for (const id of ["settingsMinimaxModel", "settingsSiliconflowModel", "settingsModelscopeModel"]) {
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

// 设置直接显示在页面中，键盘焦点落在标题，正常链接经保存保护返回。
assert.doesNotMatch(js, /\$\("settingsDialog"\)\.showModal\(\)/);
assert.match(js, /\$\("settingsTitle"\)\.focus\(\{ preventScroll: true \}\)/);
assert.match(html, /id="settingsReturn"[^>]*href="\/library"/);
assert.match(js, /async function prepareSettingsLeave\(\)/);
assert.match(js, /await modelSaving/);
assert.match(js, /hasUnsavedChanges\?\.\(\)/);
assert.match(js, /await window\.LibraryAISettings\.discard\(\)/);
assert.match(css, /\.settings-home \{ max-width: 1080px/);
assert.match(css, /\.settings-nav \{ display: flex; flex-wrap: wrap/);

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

// 档位控件移除。修改读题模型只发送角色和模型ID，不能默认覆盖旧plans。
assert.doesNotMatch(html, /settingsMinimaxPlan|同时读题数量|MiniMax 会员档位/);
assert.doesNotMatch(js, /plans: \{ minimax: \$\("settingsMinimaxPlan"\)\.value \}/);
assert.match(js, /if \(!select \|\| !\$\("settingsMinimaxPlanNote"\)\) return;/);
const readModelSource = js.slice(js.indexOf("  function readModelSettings()"), js.indexOf("  function showModelSaveResult"));
const values = { settingsPrimaryModel: "assistant", settingsCheckerModel: "auto", settingsArbiterModel: "primary", settingsMinimaxModel: "ModelA", settingsSiliconflowModel: "ModelB", settingsModelscopeModel: "ModelC" };
const payload = require("node:vm").runInNewContext(readModelSource + "\nreadModelSettings();", { $: id => {
  assert.notEqual(id, "settingsMinimaxPlan", "removed controls must never be read");
  return { value: values[id] };
} });
assert.deepEqual(JSON.parse(JSON.stringify(payload)), { primary: "assistant", checker: "auto", arbiter: "primary", models: { minimax: "ModelA", siliconflow: "ModelB", modelscope: "ModelC" } });
assert.equal(Object.hasOwn(payload, "plans"), false);

// 配置说明由用户主动展开；不承诺费用、额度或识读准确率。
assert.match(html, /<details id="settingsFreePlan" class="free-plan">[\s\S]*?AI 助手读题[\s\S]*?mineru\.net[\s\S]*?modelscope\.cn[\s\S]*?<\/details>/);
assert.doesNotMatch(js, /\$\("settingsFreePlan"\)\.open = !s\.upload_enabled/);
assert.match(html, /id="settingsModels" class="settings-section settings-models"/);
assert.match(js, /origin_split: "提取题源", chinese_quotes: "统一中文引号", subquestions: "显示小问数"/);
assert.match(css, /\.free-plan \{/);
assert.match(js, /\{ value: "assistant", label: "AI 助手读题/);
assert.match(js, /const assistant = \$\("settingsPrimaryModel"\)\.value === "assistant";\s*\$\("settingsCheckerModel"\)\.disabled = assistant;\s*\$\("settingsArbiterModel"\)\.disabled = assistant;/);
assert.match(js, /models:\s*\{[\s\S]*?modelscope:\s*\$\("settingsModelscopeModel"\)\.value\.trim\(\)/);
for (const id of ["settingsModelscopeState"]) {
  assert.match(html, new RegExp(`id="${id}"`));
  assert.match(js, new RegExp(`setApiState\\("${id}"`));
}
// 选的那家没填密钥时显示实际读题的那家，而不是一个读不了的选项。
assert.match(js, /if \(chosen && chosen\.available === false && engines\.primary\) primary = engines\.primary;/);

console.log("settings UI static checks: OK");
