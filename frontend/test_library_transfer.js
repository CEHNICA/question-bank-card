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
  auto = { backups: [{ id: "题有据自动备份-20261020-050000", name: "题有据自动备份-20261020-050000.zip", size: 12 * 1024 * 1024, created_at: "2026-10-20T05:00:00" }], due: false, keep: 2, total_bytes: 12 * 1024 * 1024 } } = {}) {
  const dom = makeDom();
  const calls = [];
  const context = vm.createContext({
    window: { location: { pathname: "/settings" } },
    document: dom.document,
    FormData: class { append() {} },
    fetch: async (url, options = {}) => {
      calls.push({ url, options });
      if (url === "/api/backup/choose-directory") return { ok: true, json: async () => choose };
      if (url === "/api/backup/export") return { ok: true, json: async () => exportBody };
      if (url === "/api/backup/inspect") return { ok: true, json: async () => inspect };
      if (url === "/api/backup/import") return { ok: true, json: async () => merge };
      if (url === "/api/backup/records" && options.method === "POST") return { ok: true, json: async () => ({ removed: { papers: 1, questions: 2, solutions: 3 } }) };
      if (url === "/api/backup/records") return { ok: true, json: async () => records };
      if (url === "/api/backup/auto" && options.method === "POST") return { ok: true, json: async () => ({ name: "题有据自动备份-20261023-050000.zip", backups: auto.backups }) };
      if (url === "/api/backup/auto") return { ok: true, json: async () => auto };
      if (url === "/api/backup/auto/inspect") return { ok: true, json: async () => inspect };
      if (url === "/api/backup/auto/import") return { ok: true, json: async () => merge };
      if (url === "/api/settings/features") return { ok: true, json: async () => ({ features: { auto_backup: true } }) };
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
  const { dom, window } = boot({ records: { records: [{ id: "1", created_at: "2026-10-22T09:00:00Z", papers: 1, questions: 20, solutions: 2, tags: 4 }] } });
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

// 9. 1.26.17：本机自动备份 —— 列出备份包、现在打一份、从某一份恢复。
//    1.26.21：正中间不再摆程序里的文件名，改成「哪一天的那一份 + 多大」；
//    并说明一共留几份、共占多少磁盘。
{
  const { dom, calls, window } = boot();
  window.LibraryTransfer.mount(dom.document.getElementById("transferSettingsMount"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const box = dom.document.getElementById("transferAuto");
  const summary = text(box);
  assert.match(summary, /从这一份恢复/, "每一份备份都能从这里恢复");
  assert.match(summary, /现在备份一次/, "也能随时手动打一份");
  assert.match(summary, /一共 1 份/, "要说清楚一共几份");
  assert.match(summary, /12\.0 MB/, "要说清楚这些包一共占多少磁盘");
  assert.doesNotMatch(summary, /题有据自动备份-20261020-050000\.zip/, "正中间不摆程序里的文件名");
  const label = box.children[0].children[0].children[0];
  assert.equal(label.title, "题有据自动备份-20261020-050000.zip", "文件名放在提示里，不占正中间");
  const called = calls.filter((call) => call.url === "/api/backup/auto");
  assert.ok(called.length, "读了一次自动备份列表");
}

// 9 之二 1.26.26：老师 10 月 24 日说这一区「介绍的太麻烦，简洁点，就是备份两次就可以了」。
// 这一版量的是**页面上真印出来的那几句话**：说清留两份就够，同一件事别反复说。
{
  const { dom, window } = boot();
  window.LibraryTransfer.mount(dom.document.getElementById("transferSettingsMount"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const box = dom.document.getElementById("transferAuto");
  const summary = text(box);
  const whole = text(dom.document.getElementById("transferSettingsMount"));
  assert.match(whole, /只留最近两份（今天和昨天）/, "一句话就说清留几份");
  assert.match(summary, /一共 1 份，占 12\.0 MB。旧的自动删掉。/, "眼下有几份、多大，一句说完");
  // 同一件事不许在三处各说一遍。
  assert.equal((whole.match(/不联网、不上传/g) || []).length, 1, "「不联网、不上传」只说一次");
  assert.equal((whole.match(/今天和昨天/g) || []).length, 1, "「今天和昨天」只说一次");
  assert.doesNotMatch(whole, /题库再大十倍/, "「题库再大十倍」那句解释删掉");
  assert.doesNotMatch(whole, /10 份/, "份数不能写死成别的数");
  // 开关旁边那句也压短了，但「救什么」这件事还得说。
  assert.match(summary, /救的是「题库自己坏了」/, "说清楚它到底救什么");
  assert.doesNotMatch(summary, /升级出错/, "「升级出错」那句删掉");
  // 打开开关后那句也得按真数说，不能再写死。
  const toggle = box.children.find((child) => child.className === "settings-switch");
  toggle.children[0].checked = true;
  await toggle.children[0].listeners.change[0]();
  assert.match(dom.document.getElementById("transferStatusauto").textContent, /留最近 2 份/,
    "打开时说的是后台给的真实份数");
}

// 10. 1.26.22：自动备份可以关掉 —— 用不上就别占磁盘；关掉以后手动那份照旧能用。
{
  const { dom, calls, window } = boot();
  window.LibraryTransfer.mount(dom.document.getElementById("transferSettingsMount"));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const box = dom.document.getElementById("transferAuto");
  const summary = text(box);
  assert.match(summary, /每天自动打一份备份/, "有一个开关");
  assert.match(summary, /题库自己坏了/, "说清楚它到底救什么");
  const toggle = box.children.find((child) => child.className === "settings-switch");
  assert.ok(toggle, "开关行在卡片里");
  const input = toggle.children[0];
  assert.equal(input.checked, true, "默认是开着的");
  input.checked = false;
  await input.listeners.change[0]();
  const saved = calls.filter((call) => call.url === "/api/settings/features");
  assert.equal(saved.length, 1, "关掉的时候要存到开关里");
  assert.deepEqual(JSON.parse(saved[0].options.body), { features: { auto_backup: false } });
  assert.match(dom.document.getElementById("transferStatusauto").textContent, /已关闭/);
}

console.log("设置 → 数据备份与迁移：所有断言通过");
}
main();



