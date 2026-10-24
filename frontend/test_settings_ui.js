"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const libraryHtml = fs.readFileSync(path.join(__dirname, "library.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "styles.css"), "utf8");
const layout = fs.readFileSync(path.join(__dirname, "original-paper-layout.js"), "utf8");

// 1.26.15：API 配置页顶上有三步进度（云端切题／看图读题／生成标签和答案），
// 打开这一页就知道配到哪一步了；重新排版单独一个样式文件，方便整页回退。
assert.match(html, /id="settingsServiceSteps"/, "the API page shows a three-step progress track");
assert.match(html, /apiStepTrack-mineru/, "step one is the cloud cut");
assert.match(html, /apiStepTrack-reader/, "step two is the reader");
assert.match(html, /apiStepTrack-answers/, "step three is the answers");
assert.match(html, /api-redesign\.css/, "the redesigned layout lives in its own stylesheet");
// 1.26.18：「删除密钥」原来绝对定位在卡片左下角，正好压在「测试连接」按钮上
// （只有存过密钥的账号才看得到，所以平时发现不了）。
const redesign = fs.readFileSync(path.join(__dirname, "api-redesign.css"), "utf8");
const actionsRule = redesign.slice(
  redesign.indexOf("#credentialUi .credential-service .credential-service-actions")
);
assert.ok(actionsRule.length, "the delete-button row is styled in the redesign stylesheet");
assert.doesNotMatch(actionsRule.slice(0, actionsRule.indexOf("}")), /position:\s*absolute/,
  "删除密钥不再绝对定位 —— 绝对定位会压住测试连接按钮");
assert.match(actionsRule.slice(0, actionsRule.indexOf("}")), /position:\s*static/);
// 1.26.16：每个区一句引导，页头按区换。
assert.match(html, /id="settingsPageLead"/, "the page header carries a per-area lead sentence");
const leads = js.slice(js.indexOf("const SETTINGS_AREA_LEADS"), js.indexOf("};", js.indexOf("const SETTINGS_AREA_LEADS")));
for (const area of ["settingsGeneral", "settingsDisplay", "settingsTransfer", "settingsReview", "settingsAbout"]) {
  assert.ok(leads.includes(area), `每个区都有一句引导：${area}`);
}

// 两个页面和安装版设置使用同一公开品牌，不再露出旧工程名。
for (const page of [html, libraryHtml]) {
  assert.match(page, /<strong>题有据<\/strong>/);
  assert.match(page, /class="brand-mark"[^>]*src="\/favicon\.png"/);
  assert.doesNotMatch(page, /题库题卡版/);
}
assert.match(html, /id="aboutVersion"/);
// 1.25.29：只报版本号。原来还缀着「（本机安装）」，在浏览器里跑源码时这句话是假的。
assert.match(js, /题有据 \$\{s\.app_version\}`/);
assert.doesNotMatch(js, /（本机安装）/);

// 两页进入一个常规设置页；API 配置只有一个外层分区和入口。
// 1.25.29：菜单项前面加了图标，所以「>设置</a>」中间允许有 svg。
assert.match(html, /id="settingsButton" href="\/settings">[\s\S]*?设置<\/a>/);
assert.match(libraryHtml, /href="\/settings">[\s\S]*?设置<\/a>/);
assert.doesNotMatch(libraryHtml, /data-library-ai-settings/);
for (const id of ["settingsGeneral", "settingsDisplay", "settingsReview", "settingsAbout"]) {
  assert.match(html, new RegExp(`data-settings-tab="${id}"`));
  assert.match(html, new RegExp(`id="${id}" class="settings-page" role="tabpanel"`));
}
assert.match(html, /id="settingsInterface"/);

// 「设置 → API 配置」以前整页只有一句说明和一个「配置 API」按钮。走进来的人问的
// 是「这台电脑现在能不能用、缺哪家的密钥」，页面一个字都不答。这里跑的是真的那个
// 函数，不是去正则查它写了哪句话 —— 换了措辞但答案仍然是空的，断言要照样失败。
assert.match(html, /id="settingsServiceReadiness"[^>]*role="status"/, "the API tab answers the readiness question in place");
const readinessSource = js.slice(js.indexOf("  function serviceReadiness("), js.indexOf("  function renderServiceReadiness("));
assert.notEqual(readinessSource.indexOf("function serviceReadiness("), -1, "serviceReadiness was moved; update this slice");
const readiness = require("node:vm").runInNewContext(
  readinessSource + "\n;({ serviceReadiness, get: () => null });",
  { state: { status: null } });
const full = readiness.serviceReadiness({
  upload_enabled: true, automatic_parse_ready: true, mineru: true,
  reader: "MiniMax-M3", reader_readiness: { ready: true, label: "MiniMax-M3" } });
assert.equal(full.tone, "ready");
assert.match(full.text, /可以直接上传/, "all services ready still says what you can do");
assert.match(full.text, /自动切题用 MinerU/, "and which service is doing the cutting");
assert.match(full.text, /MiniMax-M3/, "and which one is doing the reading");
const noMineru = readiness.serviceReadiness({
  upload_enabled: true, automatic_parse_ready: true, mineru: false,
  reader_readiness: { ready: false, label: "Qwen3-VL" } });
assert.equal(noMineru.tone, "missing");
assert.match(noMineru.text, /还没配 MinerU/, "a missing key is named, not just flagged");
assert.match(noMineru.text, /Qwen3-VL/, "the chosen-but-keyless service is named too");
assert.match(noMineru.text, /不影响上传/, "and the reader is told what still works without it");
assert.match(readiness.serviceReadiness({ upload_enabled: false, mineru: false, reader_readiness: { ready: false } }).text,
  /还不能上传资料/, "the page says so when uploading itself is off");
assert.equal(readiness.serviceReadiness(null).tone, "plain", "before the status arrives the page waits instead of guessing");
assert.match(js, /renderServiceReadiness\(\);/, "refreshing the status refreshes that line");
assert.match(js, /function showSettingsTab\(id, \{ updateHash = true \} = \{\}\)/);
assert.match(html, /<section id="settingsDialog" class="settings-panel"/);
assert.doesNotMatch(html, /<dialog id="settingsDialog"|id="reopenSettings"/);
assert.match(html, /id="libraryAIAPISettingsMount"/);
assert.match(js, /LibraryAISettings\.mount\(\$\("libraryAIAPISettingsMount"\), \{ embedded: true, confirm: confirmDialog \}\)/);
assert.doesNotMatch(html + js, /libraryAISettingsMount/);
// 外层只给真实配置入口，不再重复四家服务的密钥状态清单。
assert.match(html, /data-settings-tab="settingsGeneral"[^>]*>API 配置</);
// 1.26.9：API 配置直接平铺在设置页上，不再是"一个按钮 + 一个弹窗"，也没有标签页了。
assert.match(html, /id="settingsServicesTitle"[^>]*>API 配置</);
assert.match(html, /id="settingsCredentialHost"/);
assert.doesNotMatch(html, /id="settingsCredentialOpen"/);
assert.doesNotMatch(html, /credential-tabs|credentialReadingTab|credentialAnswerTab/);
const outerAPI = html.slice(html.indexOf('id="settingsGeneral"'), html.indexOf('id="settingsDisplay"'));
assert.doesNotMatch(outerAPI, /api-status|settings(?:Mineru|Modelscope|Minimax|Siliconflow)State|settingsFreePlan/);
assert.match(html, /云端自动切题必填/);
assert.match(html, /只手工框题或直接审核原图时，可以跳过这两步/);
assert.match(html, /id="credentialVisionProvider"[\s\S]*?value="modelscope"[\s\S]*?value="minimax"[\s\S]*?value="siliconflow"[\s\S]*?value="assistant"/);
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
  showModelSaveResult: () => {}, loadStatus: () => {}, showCredentialInline: () => {},
  leaveFor: (url) => navigated.push(url), anyDialogOpen: () => $("credentialDialog").open,
  requestAnimationFrame: (fn) => fn() });
  vm.runInContext(routeSource + openSource, context);
  return { $, context, tabs, pages, location, opened, navigated, listeners,
    selected() { return tabs.filter((tab) => tab.getAttribute("aria-selected") === "true").map((tab) => tab.id); },
    visible() { return pages.filter((page) => !page.hidden).map((page) => page.id); }
  };
}
for (const hash of ["#ai", "#api", "#services", "", "#display", "#transfer", "#help", "#about", "#unknown"]) {
  const entry = routes(hash);
  entry.context.openSettings();
  const expected = ({ "#display": "settingsDisplay", "#transfer": "settingsTransfer", "#help": "settingsReview", "#about": "settingsAbout" })[hash] || "settingsGeneral";
  assert.deepEqual(entry.selected(), [expected]); assert.deepEqual(entry.visible(), [expected]);
  assert.deepEqual(entry.opened, [], "1.26.9：旧书签不再另开一个窗口 —— 配置就摆在设置页上");
  assert.equal(entry.location.hash, hash, "Entry does not replace legacy bookmark before routing");
  for (const nextHash of ["#ai", "#api", "#services", "#display"]) {
    entry.opened.length = 0; entry.location.hash = nextHash; entry.listeners.get("hashchange")();
    assert.deepEqual(entry.selected(), [nextHash === "#display" ? "settingsDisplay" : "settingsGeneral"]);
    assert.deepEqual(entry.visible(), entry.selected());
    assert.deepEqual(entry.opened, [], "1.26.9：改了旧书签也一样，不再另开窗口");
  }
}
const navigation = routes(); navigation.context.openSettings();
assert.deepEqual(navigation.tabs.map((tab) => tab.textContent), ["API 配置", "显示与导出", "数据备份与迁移", "帮助", "关于"]);
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

// 1.13.1：题库里还有活题才拦删除，撤回完就应该能删。
// 提示必须给出路（先去题库撤回），不能只丢一句“不能删除”让人反复试。
assert.match(js, /if \(\(paper\.counts\?\.published \|\| 0\) > 0\) \{[\s\S]*?先在正式题库撤回这几道，才能删除任务/);
assert.match(js, /`已有 \$\{published\} 道题在正式题库里。先在正式题库撤回这几道，就能删除这项任务；撤回后题面快照仍留在题库。`/);
assert.doesNotMatch(js, /为保留来源追溯只能归档/);
assert.doesNotMatch(js, /已有正式题库记录的任务不能删除/);
// 确认框要说明删掉任务不会毁掉题库里那些撤回记录的题面快照。
assert.match(js, /题库里还留着的、已经撤回的那些题的题面快照不受影响，仍然查得到。/);

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

// 手工切题统一由原卷校正器保存；题组选择和 group_id 都在同一布局请求中。
assert.match(layout, /groups\(\)\.forEach\(\(g\) => \{ const o = el\("option"/);
assert.match(layout, /group_id: t\.group_id/);
assert.match(js, /openPageDialog\("view"/);
assert.doesNotMatch(js, /api\/papers\/\$\{state\.paperId\}\/questions/);

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

// 获取密钥链接放在实际填写步骤，模型细节由用户主动展开。
assert.doesNotMatch(html, /id="settingsFreePlan"/);
assert.match(html, /href="https:\/\/mineru\.net\/apiManage\/token"/);
assert.match(html, /href="https:\/\/www\.modelscope\.cn\/my\/myaccesstoken"/);
assert.match(html, /id="settingsModels" class="settings-section settings-models"/);
assert.match(js, /origin_split: "提取题源", chinese_quotes: "统一中文引号", subquestions: "显示小问数"/);
assert.match(css, /\.free-plan \{/);
assert.match(js, /\{ value: "assistant", label: "原图审核或 AI 助手/);
assert.match(js, /settingsPrimaryModel"\)\.value/);
assert.match(js, /models:\s*\{[\s\S]*?modelscope:\s*\$\("settingsModelscopeModel"\)\.value\.trim\(\)/);
assert.doesNotMatch(html, /id="settingsModelscopeState"/);
// 选的那家没填密钥时显示实际读题的那家，而不是一个读不了的选项。
assert.match(js, /if \(chosen && chosen\.available === false && engines\.primary\) primary = engines\.primary;/);

console.log("settings UI static checks: OK");
