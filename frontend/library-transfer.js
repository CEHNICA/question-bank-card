/* 设置 → 数据备份与迁移：把整库打包带走，或把另一台电脑的题库并进来。
 *
 * 每一句都写给人看：做了什么、动了几样、接下来点哪里。
 * 合并是"只增不减"的：备份里有的加进来，本机有的留着；两边都有答案时两份
 * 解析都留着（题有据的解析本来就是一串版本）。
 */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let host = null, busy = false, chosen = "", pending = null;

  function el(tag, className = "", text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  async function json(url, options = {}) {
    const response = await fetch(url, { cache: "no-store", ...options });
    let body = null;
    try { body = await response.json(); } catch { body = null; }
    if (!response.ok) throw new Error(body?.error || "这次操作没完成，请重试。");
    return body || {};
  }

  function post(url, payload) {
    return json(url, { method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" },
      body: JSON.stringify(payload) });
  }

  function postFile(url, file) {
    const form = new FormData();
    form.append("file", file);
    return json(url, { method: "POST", headers: { "X-QB-Request": "1" }, body: form });
  }

  function megabytes(size) {
    if (!Number.isFinite(size)) return "";
    return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(size / 1024))} KB`;
  }

  function moment(iso) {
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { hour12: false });
  }

  // ---- 导出 --------------------------------------------------------

  function renderExport() {
    const box = $("transferExport");
    if (!box) return;
    box.replaceChildren();
    const row = el("div", "transfer-actions");
    const pick = el("button", "button button-small", chosen ? "换个文件夹" : "选择文件夹");
    pick.type = "button";
    pick.disabled = busy;
    pick.addEventListener("click", async () => {
      if (busy) return;
      try {
        const body = await post("/api/backup/choose-directory", {});
        if (body.cancelled) return;
        chosen = body.directory || "";
        renderExport();
      } catch (error) { status("export", error.message, true); }
    });
    const run = el("button", "button button-primary button-small", "导出备份");
    run.type = "button";
    run.disabled = busy;
    run.addEventListener("click", async () => {
      if (busy) return;
      if (!chosen) { status("export", "先选一个文件夹，备份会放在那里。", true); return; }
      busy = true; renderExport();
      status("export", "正在打包，可能要等一会儿…");
      try {
        const body = await post("/api/backup/export", { directory: chosen });
        status("export", `已导出：${body.papers} 套卷子、${body.questions} 道题，${megabytes(body.size)}（含原卷照片）。文件在 ${body.file}`, false);
      } catch (error) { status("export", error.message, true); }
      finally { busy = false; renderExport(); }
    });
    row.append(pick, run);
    box.append(row);
    if (chosen) box.append(el("p", "settings-footnote", `放在：${chosen}`));
  }

  // ---- 合并 --------------------------------------------------------

  function renderMerge() {
    const box = $("transferMerge");
    if (!box) return;
    box.replaceChildren();
    const row = el("div", "transfer-actions");
    const label = el("label", "button button-small", "选择备份包");
    const input = el("input");
    input.type = "file"; input.accept = ".zip,application/zip"; input.hidden = true;
    input.addEventListener("change", async () => {
      const file = input.files?.[0];
      input.value = "";
      if (!file || busy) return;
      busy = true; renderMerge();
      status("merge", "正在读这个备份包…");
      try {
        const body = await postFile("/api/backup/inspect", file);
        pending = { file, body };
        renderPreview();
      } catch (error) { status("merge", error.message, true); pending = null; }
      finally { busy = false; renderMerge(); }
    });
    label.append(input);
    row.append(label);
    const hint = el("p", "settings-footnote", "选一个题有据导出的备份包（.zip）。先看会发生什么，确认后才真的合并。");
    box.append(row, hint);
    if (pending) renderPreview();
  }

  function renderPreview() {
    const box = $("transferPreview");
    if (!box) return;
    box.replaceChildren();
    if (!pending) { box.hidden = true; return; }
    box.hidden = false;
    const body = pending.body, report = body.report || {};
    const card = el("div", "transfer-preview");
    card.append(el("h4", "", "这份备份里有什么"));
    const list = el("ul", "transfer-facts");
    list.append(el("li", "", `${body.papers} 套卷子、${body.questions} 道题、${body.tags} 个标签`));
    if (body.created_at) list.append(el("li", "", `导出于 ${moment(body.created_at)}${body.machine ? "，" + body.machine : ""}`));
    if (body.app_version) list.append(el("li", "", `题有据 ${body.app_version} 导出的`));
    card.append(list);
    const changes = [];
    if (report["新增卷子"]) changes.push(`新增 ${report["新增卷子"]} 套卷子`);
    if (report["新增题目"]) changes.push(`新增 ${report["新增题目"]} 道题`);
    if (report["补上答案"]) changes.push(`给本机 ${report["补上答案"]} 道题补上答案或解析`);
    if (report["两边都有保留本机"]) changes.push(`${report["两边都有保留本机"]} 道题两边都有，保留本机的`);
    if (report["两版解析都留着"]) changes.push(`${report["两版解析都留着"]} 道题两边都有解析，两个版本都留着`);
    if (report["题号一样内容不同"]) changes.push(`${report["题号一样内容不同"]} 道题题号一样但内容不同，两道都留着`);
    if (report["新增标签"]) changes.push(`新增 ${report["新增标签"]} 个标签`);
    if (report["多贴的标签"]) changes.push(`${report["多贴的标签"]} 道题多贴了标签`);
    card.append(el("p", "transfer-summary", changes.length ? "合并后会：" + changes.join("；") + "。本机原有的题一道不删。" : "这份备份里的东西本机都有了，合并了也不会变。"));
    const actions = el("div", "transfer-actions");
    const go = el("button", "button button-primary button-small", "合并进来");
    go.type = "button";
    go.disabled = busy;
    go.addEventListener("click", async () => {
      if (busy || !pending) return;
      busy = true; renderMerge();
      status("merge", "正在合并，请不要关掉题有据…");
      try {
        const body = await postFile("/api/backup/import", pending.file);
        pending = null;
        renderPreview();
        status("merge", mergeReport(body.report), false);
        void loadRecords();
      } catch (error) { status("merge", error.message, true); }
      finally { busy = false; renderMerge(); }
    });
    const cancel = el("button", "button button-quiet button-small", "先不合");
    cancel.type = "button";
    cancel.addEventListener("click", () => { pending = null; renderPreview(); status("merge", "没有动本机题库。", false); });
    actions.append(go, cancel);
    card.append(actions);
    box.append(card);
  }

  function mergeReport(report = {}) {
    const parts = [];
    if (report["新增卷子"]) parts.push(`新增 ${report["新增卷子"]} 套卷子`);
    if (report["新增题目"]) parts.push(`新增 ${report["新增题目"]} 道题`);
    if (report["补上答案"]) parts.push(`给本机 ${report["补上答案"]} 道题补上了答案或解析`);
    if (report["两边都有保留本机"]) parts.push(`${report["两边都有保留本机"]} 道题两边都有，保留了本机的版本`);
    if (report["两版解析都留着"]) parts.push(`${report["两版解析都留着"]} 道题两边都有解析，两个版本都留着`);
    if (report["题号一样内容不同"]) parts.push(`${report["题号一样内容不同"]} 道题题号一样但内容不同，两道都留着了`);
    if (report["新增标签"]) parts.push(`新增 ${report["新增标签"]} 个标签`);
    if (report["多贴的标签"]) parts.push(`${report["多贴的标签"]} 道题多贴了标签`);
    if (!parts.length) return "合并完成：没有需要补的东西，本机题库保持原样。";
    return "合并完成：" + parts.join("；") + "。本机原有的题一道没删。";
  }

  // ---- 导入记录 ----------------------------------------------------

  async function loadRecords() {
    const box = $("transferRecords");
    if (!box) return;
    try {
      const body = await json("/api/backup/records");
      const records = body.records || [];
      box.replaceChildren();
      if (!records.length) {
        box.append(el("p", "settings-footnote", "还没有合并过备份。"));
        return;
      }
      const list = el("ul", "transfer-records");
      records.slice(0, 8).forEach((record) => {
        const item = el("li");
        const bits = [];
        if (record.papers) bits.push(`${record.papers} 套卷子`);
        if (record.questions) bits.push(`${record.questions} 道题`);
        if (record.solutions) bits.push(`${record.solutions} 份解析`);
        if (record.filled) bits.push(`给 ${record.filled} 道题补上答案`);
        if (record.tags) bits.push(`${record.tags} 个标签`);
        item.append(el("span", "", `${moment(record.created_at)}　并进来 ${bits.join("、") || "一些题目"}`));
        list.append(item);
      });
      box.append(list);
      const undo = el("button", "button button-small", "撤销最近一次合并");
      undo.type = "button";
      undo.disabled = busy;
      undo.addEventListener("click", async () => {
        if (busy) return;
        busy = true;
        try {
          const body = await post("/api/backup/records", {});
          const removed = body.removed || {};
          const parts = [];
          if (removed.papers) parts.push(`${removed.papers} 套卷子`);
          if (removed.questions) parts.push(`${removed.questions} 道题`);
          if (removed.solutions) parts.push(`${removed.solutions} 份解析`);
          if (removed.filled) parts.push(`把 ${removed.filled} 道题补上的答案还原了`);
          if (removed.tags) parts.push(`${removed.tags} 个标签`);
          status("records", parts.length ? `已撤销：删掉 ${parts.join("、")}。` : "已撤销：这次合并没有留下改动。", false);
          await loadRecords();
        } catch (error) { status("records", error.message, true); }
        finally { busy = false; }
      });
      box.append(undo);
    } catch (error) {
      box.replaceChildren(el("p", "settings-footnote", "导入记录暂时读不出来。"));
    }
  }

  function status(slot, message, isError) {
    const node = $(`transferStatus${slot}`);
    if (!node) return;
    node.textContent = message || "";
    node.classList.toggle("is-error", Boolean(isError));
  }

  async function mount(target) {
    host = target || host;
    if (!host) return;
    host.replaceChildren();
    const intro = el("p", "settings-description",
      "题库全在这台电脑上。换电脑、备份，或者把两台电脑的题库并到一起，都在这里。备份不联网，也不上传。");
    const exportBox = el("section", "settings-section");
    exportBox.append(el("h3", "", "导出备份"), el("p", "settings-footnote", "把整库（含原卷照片）打成一个包，放到你自己选的文件夹里。"));
    const exportInner = el("div");
    exportInner.id = "transferExport";
    const exportStatus = el("p", "transfer-status");
    exportStatus.id = "transferStatusexport";
    exportBox.append(exportInner, exportStatus);

    const mergeBox = el("section", "settings-section");
    mergeBox.append(el("h3", "", "从备份合并"), el("p", "settings-footnote",
      "把另一台电脑（或以前导出的）备份包并进来。备份里有的加进来，本机有的留着；本机原有的题一道不删。"));
    const mergeInner = el("div");
    mergeInner.id = "transferMerge";
    const preview = el("div");
    preview.id = "transferPreview";
    preview.hidden = true;
    const mergeStatus = el("p", "transfer-status");
    mergeStatus.id = "transferStatusmerge";
    mergeBox.append(mergeInner, preview, mergeStatus);

    const recordsBox = el("section", "settings-section");
    recordsBox.append(el("h3", "", "导入记录"), el("p", "settings-footnote", "每次合并都记着，合并错了可以撤销最近一次。"));
    const records = el("div");
    records.id = "transferRecords";
    const recordsStatus = el("p", "transfer-status");
    recordsStatus.id = "transferStatusrecords";
    recordsBox.append(records, recordsStatus);

    host.append(intro, exportBox, mergeBox, recordsBox);
    renderExport(); renderMerge();
    void loadRecords();
  }

  window.LibraryTransfer = { mount, refresh: loadRecords };
})();
