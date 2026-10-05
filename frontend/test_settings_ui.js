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

// 两页进入一个常规设置页；API 配置只有一个外层分区和入口。
assert.match(html, /id="settingsButton" href="\/settings">设置<\/a>/);
assert.match(libraryHtml, /href="\/settings">设置<\/a>/);
assert.doesNotMatch(libraryHtml, /data-library-ai-settings/);
for (const id of ["settingsGeneral", "settingsDisplay", "settingsReview", "settingsAbout"]) {
  assert.match(html, new RegExp(`data-settings-tab="${id}"`));
  assert.match(html, new RegExp(`id="${id}" class="settings-page" role="tabpanel"`));
}
assert.match(html, /id="settingsInterface"/);
assert.match(js, /function showSettingsTab\(id, \{ updateHash = true \} = \{\}\)/);
assert.match(html, /<section id="settingsDialog" class="settings-panel"/);
assert.doesNotMatch(html, /<dialog id="settingsDialog"|id="reopenSettings"/);
assert.match(html, /id="libraryAIAPISettingsMount"/);
assert.match(js, /LibraryAISettings\.mount\(\$\("libraryAIAPISettingsMount"\), \{ embedded: true, confirm: confirmDialog \}\)/);
assert.doesNotMatch(html + js, /libraryAISettingsMount/);
// 1.12.6: “API 配置” 曾在一屏里出现三次（左侧 tab、内容区标题、底部按钮），
// 看着分不清哪个能点。现在三处各有各的说法，只有底部按钮是入口。
assert.doesNotMatch(html, />API 配置<\/button>/, "No entry may still be called plain “API 配置”");
assert.doesNotMatch(html, /id="settingsServicesTitle"[^>]*>API 配置</, "The section heading is not “API 配置” either");
assert.match(html, /data-settings-tab="settingsGeneral"[^>]*>服务与密钥</);
assert.match(html, /id="settingsServicesTitle"[^>]*>读题与看图服务</);
assert.match(html, /id="settingsCredentialOpen"[^>]*>打开密钥窗口<\/button>/);
assert.doesNotMatch(html + js, /settingsAI\b|settingsAPIOpen/);
assert.match(js, /window\.APISettings = Object\.freeze\(\{ open: openCredentialSettings \}\)/);

// Run the actual settings route and navigation handlers against the tabs parsed
// from the page. Old answer bookmarks must work on both entry and hash changes.
const vm = require("node:vm"), dom = require("./credential-test-dom.js");
// 1.12.9：那行提示和 renderSettingsReady 一起删了，切片终点改用下一个函数。
// 注意：indexOf 找不到会返回 -1，slice(a, -1) 会悄悄切掉最后一个字符还不报错 —���
// 换终点时一定要确认这个字符串还在。
const routeSource = js.slice(js.indexOf("  const SETTINGS_HASHES = {"), js.indexOf("  async function loadFeatureSwitches()"));
const openSource = js.slice(js.indexOf("  function openSettings()"), js.indexOf('  $("settingsButton").addEventListener'));
function routes(hash = "") {
  const listeners = new Map(), nodes = new Map(), opened = [], navigated = [];
  const $ = (id) => { if (!nodes.has(id)) nodes.set(id, dom.node(id)); return nodes.get(id); };
  const tabs = [...html.matchAll(/<button[^>]*data-settings-tab="([^"]+)"[^>]*>([^<]+)<\/button>/g)].map((match) => {
    const tab = dom.node(match[1], "button", match[2]); tab.dataset = { settingsTab: match[1] }; tab.focus = () => { tab.focused = true; }; return tab;
  });
  const pages = [...html.matchAll(/id="([^"]+)" class="settings-page" role="tabpanel"/g)].map((match) => $(match[1]));
  const location = { pathname: "/settings", search: "", hash };
  const context = vm.createContext({ $, window: { location,
    addEventListener(name, fn) { listeners.set(name, fn); },
    history: { replaceState(_state, _title, url) { location.hash = url.slice(url.indexOf("#")); } }
  }, document: {
    querySelectorAll(selector) { if (selector === "[data-settings-tab]") return tabs; if (selector === "#settingsDialog .settings-page") return pages; throw new Error(selector); },
    querySelector(selector) { assert.equal(selector, "#settingsDialog .settings-scroll"); return $("scroll"); }
  }, state: { lens: false, focus: false, autoExpand: true }, modelFormDirty: false,
  openCredentialSettings(tab) { opened.push(tab); $("credentialDialog").open = true; },
  loadFeatureSwitches: () => {}, renderSettingsModels: () => {},
  showModelSaveResult: () => {}, loadStatus: () => {},
  leaveFor: (url) => navigated.push(url), anyDialogOpen: () => $("credentialDialog").open,
  requestAnimationFrame: (fn) => fn() });
  vm.runInContext(routeSource + openSource, context);
  return { $, context, tabs, pages, location, opened, navigated, listeners,
    selected() { return tabs.filter((tab) => tab.getAttribute("aria-selected") === "true").map((tab) => tab.id); },
    visible() { return pages.filter((page) => !page.hidden).map((page) => page.id); }
  };
}
for (const hash of ["#ai", "#api", "#services", "", "#display", "#help", "#about", "#unknown"]) {
  const entry = routes(hash);
  entry.context.openSettings();
  const expected = ({ "#display": "settingsDisplay", "#help": "settingsReview", "#about": "settingsAbout" })[hash] || "settingsGeneral";
  assert.deepEqual(entry.selected(), [expected]); assert.deepEqual(entry.visible(), [expected]);
  assert.deepEqual(entry.opened, ["#ai", "#api"].includes(hash) ? ["answers"] : [], "Initial bookmarks use only the unified window");
  assert.equal(entry.location.hash, hash, "Entry does not replace legacy bookmark before routing");
  for (const nextHash of ["#ai", "#api", "#services", "#display"]) {
    entry.opened.length = 0; entry.location.hash = nextHash; entry.listeners.get("hashchange")();
    assert.deepEqual(entry.selected(), [nextHash === "#display" ? "settingsDisplay" : "settingsGeneral"]);
    assert.deepEqual(entry.visible(), entry.selected());
    assert.deepEqual(entry.opened, ["#ai", "#api"].includes(nextHash) ? ["answers"] : [], "Changed legacy bookmarks also open the answer section");
  }
}
const navigation = routes(); navigation.context.openSettings();
assert.deepEqual(navigation.tabs.map((tab) => tab.textContent), ["服务与密钥", "显示与导出", "帮助", "关于"]);
for (let index = 0; index < navigation.tabs.length; index++) {
  for (const key of ["ArrowLeft", "ArrowRight"]) {
    let prevented = false;
    navigation.tabs.forEach((tab) => { tab.focused = false; });
    navigation.tabs[index].events.keydown[0]({ key, preventDefault() { prevented = true; } });
    const next = navigation.tabs[(index + (key === "ArrowRight" ? 1 : -1) + navigation.tabs.length) % navigation.tabs.length];
    assert.equal(prevented, true); assert.equal(next.focused, true);
    assert.deepEqual(navigation.selected(), [next.id]); assert.deepEqual(navigation.visible(), [next.id]);
    assert.equal(next.tabIndex, 0); assert.ok(navigation.tabs.filter((tab) => tab !== next).every((tab) => tab.tabIndex === -1));
  }
  navigation.tabs[index].events.click[0]();
  assert.deepEqual(navigation.selected(), [navigation.tabs[index].id]); assert.deepEqual(navigation.visible(), [navigation.tabs[index].id]);
}
assert.deepEqual(navigation.opened, [], "Normal outer tab navigation does not open another API entry");
navigation.location.pathname = "/library"; navigation.location.hash = "#ai";
navigation.listeners.get("hashchange")(); assert.deepEqual(navigation.opened, []);
navigation.context.openSettings(); assert.deepEqual(navigation.navigated, ["/settings"]);
// 1.12.9：这行「导入时先在本机切题……」连同 renderSettingsReady 整个删了。
// 上面四家服务的状态列表已经说清谁配了谁没配，再复述一遍内部流程没有新信息。
// 断言只钉「函数和元素不存在」——app.js 里留了一段说明为什么删的注释，
// 那是给人看的，不该被一条「文件里不许出现这几个字」的断言误伤。
assert.doesNotMatch(html, /id="settingsReady"/);
assert.doesNotMatch(js, /function renderSettingsReady/);
assert.match(js, /现在是 AI 助手读题：导入会先在本机准备原卷并尝试切题，无需 MinerU/);
assert.doesNotMatch(js, /新资料的题卡先用 MinerU 的文字/);
// 专注和放大镜两个开关在“审核界面”里，与工具栏按钮保持同步。
assert.match(html, /id="settingsFocus" type="checkbox" role="switch"/);
assert.match(js, /if \(\$\("settingsFocus"\)\) \$\("settingsFocus"\)\.checked = on;/);
// 读题模型改了就保存，不再依赖单独的保存按钮。
assert.doesNotMatch(html, /id="settingsModelSave"/);
assert.match(js, /\.forEach\(\(id\) => \$\(id\)\.addEventListener\("change", \(\) => \{ void saveModelSettings\(\); \}\)\)/);
// 当前这份试卷的操作不属于“设置”：在试卷标题旁的“试卷操作”里。
assert.doesNotMatch(html, /id="settingsTask"/);
assert.match(html, /<details class="menu paper-menu" id="paperMenu">[\s\S]*?id="settingsRename"[\s\S]*?id="settingsTaskNotes"[\s\S]*?id="settingsArchive"[\s\S]*?id="settingsDelete"/);

// 读题设置只保留一个实际读题模型；第二读和裁决入口已移除。
for (const id of ["settingsPrimaryModel"]) {
  assert.match(html, new RegExp(`id="${id}"`));
}
assert.doesNotMatch(html, /settingsCheckerModel|settingsArbiterModel|有出入时复核|两次不一致时裁决/);
for (const id of ["settingsMinimaxModel", "settingsSiliconflowModel", "settingsModelscopeModel"]) {
  assert.match(html, new RegExp(`id="${id}"[^>]*list="${id}s"`));
}
assert.match(js, /api\("\/api\/settings\/models"/);
assert.match(js, /primary:\s*\$\("settingsPrimaryModel"\)\.value/);
assert.doesNotMatch(js, /settingsCheckerModel|settingsArbiterModel/);
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
assert.doesNotMatch(js, /AI 两次一致|AI 三读多数一致/);

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
const values = { settingsPrimaryModel: "assistant", settingsMinimaxModel: "ModelA", settingsSiliconflowModel: "ModelB", settingsModelscopeModel: "ModelC" };
const payload = require("node:vm").runInNewContext(readModelSource + "\nreadModelSettings();", { $: id => {
  assert.notEqual(id, "settingsMinimaxPlan", "removed controls must never be read");
  return { value: values[id] };
} });
assert.deepEqual(JSON.parse(JSON.stringify(payload)), { primary: "assistant", models: { minimax: "ModelA", siliconflow: "ModelB", modelscope: "ModelC" } });
assert.equal(Object.hasOwn(payload, "plans"), false);

// 配置说明由用户主动展开；不承诺费用、额度或识读准确率。
assert.match(html, /<details id="settingsFreePlan" class="free-plan">[\s\S]*?AI 助手读题[\s\S]*?mineru\.net[\s\S]*?modelscope\.cn[\s\S]*?<\/details>/);
assert.doesNotMatch(js, /\$\("settingsFreePlan"\)\.open = !s\.upload_enabled/);
assert.match(html, /id="settingsModels" class="settings-section settings-models"/);
assert.match(js, /origin_split: "提取题源", chinese_quotes: "统一中文引号", subquestions: "显示小问数"/);
assert.match(css, /\.free-plan \{/);
assert.match(js, /\{ value: "assistant", label: "AI 助手读题/);
assert.match(js, /settingsPrimaryModel"\)\.value/);
assert.match(js, /models:\s*\{[\s\S]*?modelscope:\s*\$\("settingsModelscopeModel"\)\.value\.trim\(\)/);
for (const id of ["settingsModelscopeState"]) {
  assert.match(html, new RegExp(`id="${id}"`));
  assert.match(js, new RegExp(`setApiState\\("${id}"`));
}
// 选的那家没填密钥时显示实际读题的那家，而不是一个读不了的选项。
assert.match(js, /if \(chosen && chosen\.available === false && engines\.primary\) primary = engines\.primary;/);

console.log("settings UI static checks: OK");
