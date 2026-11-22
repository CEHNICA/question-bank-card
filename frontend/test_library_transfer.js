// 设置 → 数据备份与迁移：导出、合并、撤销这三件事，界面上必须说人话。
//
// 这里不测"按钮好不好看"，测的是三件要紧的事：
//   1. 合并之前一定先看一份"会发生什么"，看完再点合并；
//   2. 合并的话说得像人话：本机原有的题一道不删、两边的解析都留着；
//   3. 没选文件夹就不导出、没选备份包就不合并。
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "library-transfer.js"), "utf8");
const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");

// 最小 DOM：够跑这个文件，不引入整个测试框架。
function makeDom() {
  const make = (tag = "div") => {
    const node = {
      tagName: tag.toUpperCase(), children: [], attributes: {}, style: {}, dataset: {},
      listeners: {}, className: "", textContent: "", hidden: false, value: "", files: null,
      disabled: false, type: "", accept: "", hidden_: false,
      append(...items) { items.forEach((item) => node.children.push(item)); },
      replaceChildren(...items) { node.children = items; },
      addEventListener(name, fn) { (node.listeners[name] ||= []).push(fn); },
      click() { (node.listeners.click || []).forEach((fn) => fn()); },
      focus() { node.focused = true; },
      querySelectorAll() { return []; },
      setAttribute(name, value) { node.attributes[name] = value; },
      getAttribute(name) { return node.attributes[name]; },
      classList: { toggle() {}, add() {}, remove() {} },
    };
    node.classList.toggle = () => {};
    return node;
  };
  const nodes = new Map();
  const document = {
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, make()); return nodes.get(id); },
    createElement: make,
  };
  return { nodes, document, make };
}

function boot({ inspect = {}, merge = {}, records = { records: [] }, exportBody = {}, choose = { selected: true, directory: "D:/备份" },
  catalog = {
    papers: [{ id: "p-1", title: "高中数学思想导引", filename: "高中数学思想导引.pdf", questions: 512, publications: 500 }],
    folders: [{ id: "t-1", name: "函数与导数", kind: "knowledge", path_label: "知识点 · 函数与导数", questions: 48 }],
  } } = {}) {
  const dom = makeDom();
  const calls = [];
  const context = vm.createContext({
    window: { location: { pathname: "/settings" } },
    document: dom.document,
    FormData: class { append() {} },
    fetch: async (url, options = {}) => {
      calls.push({ url, options });
      if (url === "/api/backup/papers") return { ok: true, json: async () => catalog };
      if (url === "/api/backup/choose-directory") return { ok: true, json: async () => choose };
      if (url === "/api/backup/export") return { ok: true, json: async () => exportBody };
      if (url === "/api/backup/inspect") return { ok: true, json: async () => inspect };
      if (url === "/api/backup/import") return { ok: true, json: async () => merge };
      if (url === "/api/backup/records" && options.method === "POST") return { ok: true, json: async () => ({ removed: { papers: 1, questions: 2, solutions: 3 } }) };
      if (url === "/api/backup/records") return { ok: true, json: async () => records };
      return { ok: false, json: async () => ({ error: "不该到这里" }) };
    },
    setTimeout, clearTimeout, console,
  });
  vm.runInContext(source, context);
  return { dom, calls, window: context.window };
}

const find = (node, label) => {
  if (!node) return null;
  if ((node.textContent || "") === label) return node;
  for (const child of node.children || []) { const hit = find(child, label); if (hit) return hit; }
  return null;
};
const text = (node) => {
  const parts = [node.textContent || ""];
  (node.children || []).forEach((child) => parts.push(text(child)));
  return parts.join(" ");
};

async function main() {
// 1. 界面挂在设置里，而且有一节是"数据备份与迁移"。
assert.match(html, /data-settings-tab="settingsTransfer"[^>]*>数据备份与迁移</, "设置里要有一节专管备份和迁移");
assert.match(html, /id="transferSettingsMount"/, "这一节要有挂载点");
assert.match(html, /<script src="\/static\/library-transfer\.js" defer><\/script>/, "要加载这一节的脚本");

// 2. 没选文件夹就不导出 —— 别把备份丢到不知道哪儿去。
{
  const { dom, calls, window } = boot();
  window.LibraryTransfer.mount(dom.nodes.get("transferSettingsMount") || dom.document.getElementById("transferSettingsMount"));
  const exportBox = dom.document.getElementById("transferExport");
  const run = find(exportBox, "导出备份");
  assert.ok(run, "要有导出按钮");
  await run.listeners.click[0]();
  assert.equal(calls.filter((call) => call.url === "/api/backup/export").length, 0, "没选文件夹时不能导出");
  assert.match(dom.document.getElementById("transferStatusexport").textContent, /先选一个文件夹/);
}

// 3. 选了文件夹 → 导出，并且说清楚导出了什么。
{
  const { dom, calls, window } = boot({ exportBody: { name: "题有据备份-1.zip", file: "D:/备份/题有据备份-1.zip", size: 12 * 1024 * 1024, papers: 8, questions: 898, tags: 302, publications: 869 } });
  window.LibraryTransfer.mount(dom.document.getElementById("transferSettingsMount"));
  const exportBox = dom.document.getElementById("transferExport");
  const pick = find(exportBox, "选择文件夹");
  await pick.listeners.click[0]();
  // 选完文件夹界面会重画，按钮是新的。
  const run = find(dom.document.getElementById("transferExport"), "导出备份");
  assert.ok(run, "重新画过之后也要能找到导出按钮");
  await run.listeners.click[0]();
  assert.equal(calls.filter((call) => call.url === "/api/backup/export").length, 1, "选好文件夹就该导出");
  const status = dom.document.getElementById("transferStatusexport").textContent;
  assert.match(status, /8 套卷子/);
  assert.match(status, /898 道题/);
  assert.match(status, /D:\/备份/);
}

// 4. 选了备份包 → 先给一张"会发生什么"的卡片，合并按钮这时候才出现。
{
  const { dom, calls, window } = boot({
    inspect: { papers: 8, questions: 898, tags: 302, publications: 869, created_at: "2026-10-22T09:00:00Z", machine: "办公室电脑", app_version: "1.25.98",
      report: { "新增卷子": 1, "新增题目": 20, "补上答案": 3, "两边都有保留本机": 5, "两版解析都留着": 2, "题号一样内容不同": 1, "新增标签": 4, "多贴的标签": 6 } },
  });
  window.LibraryTransfer.mount(dom.document.getElementById("transferSettingsMount"));
  const mergeBox = dom.document.getElementById("transferMerge");
  const label = find(mergeBox, "选择备份包");
  const input = label.children.find((child) => child.type === "file");
  input.files = [{ name: "题有据备份-1.zip", size: 1024 }];
  await input.listeners.change[0]();
  assert.equal(calls.filter((call) => call.url === "/api/backup/inspect").length, 1, "选包就该先看一眼");
  assert.equal(calls.filter((call) => call.url === "/api/backup/import").length, 0, "看一眼不能就合并");
  const preview = dom.document.getElementById("transferPreview");
  const summary = text(preview);
  assert.match(summary, /新增 1 套卷子/, "要说清会多进来什么");
  assert.match(summary, /给本机 3 道题补上答案或解析/);
  assert.match(summary, /5 道题两边都有，保留本机的/);
  assert.match(summary, /2 道题两边都有解析，两个版本都留着/);
  assert.match(summary, /本机原有的题一道不删/);
  assert.match(summary, /办公室电脑/, "要说清这份备份来自哪台电脑");
  const go = find(preview, "合并进来");
  assert.ok(go, "看完预览才给合并按钮");
}

// 5. 合并完要说人话，而且说清本机没被动过。
{
  const { dom, window } = boot({
    inspect: { papers: 1, questions: 10, tags: 0, publications: 0, report: {} },
    merge: { report: { "新增卷子": 1, "新增题目": 20, "补上答案": 3, "两版解析都留着": 2, "题号一样内容不同": 1, "新增标签": 4, "多贴的标签": 6 }, record: { id: "1" } },
  });
  window.LibraryTransfer.mount(dom.document.getElementById("transferSettingsMount"));
  const mergeBox = dom.document.getElementById("transferMerge");
  const label = find(mergeBox, "选择备份包");
  const input = label.children.find((child) => child.type === "file");
  input.files = [{ name: "b.zip", size: 10 }];
  await input.listeners.change[0]();
  const preview = dom.document.getElementById("transferPreview");
  const go = find(preview, "合并进来");
  await go.listeners.click[0]();
  const status = dom.document.getElementById("transferStatusmerge").textContent;
  assert.match(status, /合并完成/);
  assert.match(status, /新增 1 套卷子/);
  assert.match(status, /两个版本都留着/);
  assert.match(status, /本机原有的题一道没删/);
}

// 6. 合并记录能撤销。
{
  const { dom, window } = boot({ records: { records: [{ id: "1", created_at: "2026-10-22T09:00:00Z", papers: 1, questions: 20, solutions: 2, tags: 4 }] } });
  window.LibraryTransfer.mount(dom.document.getElementById("transferSettingsMount"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const records = dom.document.getElementById("transferRecords");
  const undo = find(records, "撤销最近一次合并");
  assert.ok(undo, "合并过就要能撤销");
  await undo.listeners.click[0]();
  assert.match(dom.document.getElementById("transferStatusrecords").textContent, /已撤销/);
}

// 7. 1.26.16：导入记录的时间写成人话 —— "3 天前 · 10 月 20 日 09:00"，
// 而不是机器时间戳。合并记录往往是几天前的事，"几天前"才管用。
{
  const threeDaysAgo = new Date(Date.now() - 3 * 86400_000).toISOString();
  const { dom, window } = boot({ records: { records: [{ id: "1", created_at: threeDaysAgo, papers: 1, questions: 20, solutions: 2, tags: 4 }] } });
  window.LibraryTransfer.mount(dom.document.getElementById("transferSettingsMount"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const records = dom.document.getElementById("transferRecords");
  const summary = text(records);
  assert.match(summary, /前 · |今天 |昨天 /, "导入记录写成人话：今天／昨天／几天前");
  assert.doesNotMatch(summary, /\d{4}\/\d{1,2}\/\d{1,2}\s+\d{2}:\d{2}:\d{2}/, "不再出现机器时间戳");
}

// 8. 1.26.16：这一区不再自带一句引导 —— 页头统一给每个区一句。
{
  const { dom, window } = boot();
  window.LibraryTransfer.mount(dom.document.getElementById("transferSettingsMount"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const summary = text(dom.document.getElementById("transferSettingsMount"));
  assert.doesNotMatch(summary, /备份不联网，也不上传/, "页头已经说了这一区，区内不再重复");
}

// 9. 已移除「本机自动备份」区块，只保留手动「导出备份」「从备份合并」「导入记录」。
{
  const { dom, calls, window } = boot();
  window.LibraryTransfer.mount(dom.document.getElementById("transferSettingsMount"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const whole = text(dom.document.getElementById("transferSettingsMount"));
  assert.doesNotMatch(whole, /本机自动备份|每天自动打一份备份|现在备份一次/, "界面不再展示自动备份区块");
  assert.match(whole, /导出备份/, "保留手动导出备份");
  assert.match(whole, /从备份合并/, "保留从备份合并");
  assert.match(whole, /导入记录/, "保留导入记录");
  assert.equal(calls.filter((call) => call.url.startsWith("/api/backup/auto")).length, 0, "不再请求自动备份接口");
}

// 10. 按卷/按书导出与按目录文件夹导出，以及预览显示书名/文件夹名。
{
  const { dom, calls, window } = boot({
    exportBody: { name: "题有据题包-高中数学思想导引-1.zip", file: "D:/备份/题有据题包-高中数学思想导引-1.zip", size: 8 * 1024 * 1024, papers: 1, questions: 512, paper_names: ["高中数学思想导引"] },
  });
  window.LibraryTransfer.mount(dom.document.getElementById("transferSettingsMount"));
  const exportBox = dom.document.getElementById("transferExport");
  await find(exportBox, "只选几套卷子或书").listeners.click[0]();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(calls.some((call) => call.url === "/api/backup/papers"), "切到选卷模式就应读取卷子清单");
  const paperOption = find(dom.document.getElementById("transferExport"), "高中数学思想导引");
  assert.ok(paperOption, "列表里应显示书名");
  const pickerList = dom.document.getElementById("transferExport").children.find((child) => child.className === "transfer-paper-list");
  const checkbox = pickerList.children[0].children[0];
  checkbox.checked = true;
  await checkbox.listeners.change[0]();
  await find(dom.document.getElementById("transferExport"), "选择文件夹").listeners.click[0]();
  await find(dom.document.getElementById("transferExport"), "导出备份").listeners.click[0]();
  const exportCall = calls.find((call) => call.url === "/api/backup/export");
  assert.deepEqual(JSON.parse(exportCall.options.body), { directory: "D:/备份", paper_ids: ["p-1"] });
  assert.match(dom.document.getElementById("transferStatusexport").textContent, /高中数学思想导引/);
}

{
  const { dom, calls, window } = boot({
    exportBody: { name: "题有据题包-函数与导数-1.zip", file: "D:/备份/题有据题包-函数与导数-1.zip", size: 2 * 1024 * 1024, papers: 2, questions: 48, folder_names: ["函数与导数"] },
    inspect: {
      papers: 2, questions: 48, tags: 5, publications: 48,
      paper_items: [{ title: "高中数学思想导引", filename: "高中数学思想导引.pdf", questions: 48 }],
      folder_items: [{ name: "函数与导数", kind: "knowledge" }],
      report: { "新增卷子": 1, "新增题目": 48 },
    },
    records: { records: [{ id: "1", created_at: "2026-10-22T09:00:00Z", papers: 1, questions: 48, folder_names: ["函数与导数"] }] },
  });
  window.LibraryTransfer.mount(dom.document.getElementById("transferSettingsMount"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  await find(dom.document.getElementById("transferExport"), "只选目录文件夹").listeners.click[0]();
  await new Promise((resolve) => setTimeout(resolve, 0));
  const folderRow = find(dom.document.getElementById("transferExport"), "知识点 · 函数与导数");
  assert.ok(folderRow, "目录列表里应显示文件夹路径");
  const pickerList = dom.document.getElementById("transferExport").children.find((child) => child.className === "transfer-paper-list");
  const checkbox = pickerList.children[0].children[0];
  checkbox.checked = true;
  await checkbox.listeners.change[0]();
  await find(dom.document.getElementById("transferExport"), "选择文件夹").listeners.click[0]();
  await find(dom.document.getElementById("transferExport"), "导出备份").listeners.click[0]();
  const exportCall = calls.find((call) => call.url === "/api/backup/export");
  assert.deepEqual(JSON.parse(exportCall.options.body), { directory: "D:/备份", tag_ids: ["t-1"] });
  assert.match(dom.document.getElementById("transferStatusexport").textContent, /函数与导数/);

  const label = find(dom.document.getElementById("transferMerge"), "选择备份包");
  const input = label.children.find((child) => child.type === "file");
  input.files = [{ name: "bundle.zip", size: 1024 }];
  await input.listeners.change[0]();
  const previewText = text(dom.document.getElementById("transferPreview"));
  assert.match(previewText, /包含文件夹：「函数与导数」/);
  assert.match(previewText, /包含：《高中数学思想导引》（48 道题）/);
  assert.match(text(dom.document.getElementById("transferRecords")), /「函数与导数」/);
}

console.log("设置 → 数据备份与迁移：所有断言通过");
}
main();



