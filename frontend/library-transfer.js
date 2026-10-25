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

  // 1.26.16：机器时间写成人话 —— "3 天前 · 10 月 23 日 05:28"。
  // 只看得出是哪一天的记录不够，合并记录往往是几天前的事，"几天前"才管用。
  function when(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const days = Math.floor((Date.now() - date.getTime()) / 86400000);
    const stamp = `${date.getMonth() + 1} 月 ${date.getDate()} 日 ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
    if (days <= 0) return `今天 ${stamp}`;
    if (days === 1) return `昨天 ${stamp}`;
    if (days < 30) return `${days} 天前 · ${stamp}`;
    return stamp;
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
    const notes = report["说明"];
    if (Array.isArray(notes) && notes.length) {
      const list = el("ul", "transfer-facts");
      notes.slice(0, 8).forEach((note) => list.append(el("li", "", note)));
      card.append(list);
    }
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
    const notes = Array.isArray(report["说明"]) ? report["说明"] : [];
    const head = parts.length ? "合并完成：" + parts.join("；") + "。" : "合并完成：没有需要补的东西，本机题库保持原样。";
    return notes.length ? `${head} ${notes.slice(0, 3).join("；")}` : `${head} 本机原有的题一道没删。`;
  }

  // ---- 本机自动备份 --------------------------------------------------

  async function loadAutoBackups() {
    const box = $("transferAuto");
    if (!box) return;
    try {
      const body = await json("/api/backup/auto");
      const backups = body.backups || [];
      // 留几份由后台说了算（1.26.23 起是两份），话里就不写死数字。
      const keep = body.keep || 2;
      box.replaceChildren();
      if (!backups.length) {
        box.append(el("p", "settings-footnote", "还没有自动备份过。软件每天会自己打一份，也可以现在就打一份。"));
      } else {
        const list = el("ul", "transfer-records");
        backups.forEach((backup) => {
          const item = el("li");
          const restore = el("button", "button button-small", "从这一份恢复");
          restore.type = "button";
          restore.disabled = busy;
          restore.addEventListener("click", () => { void restoreAutoBackup(backup.id); });
          const label = el("span", "", `${when(backup.created_at)}　${megabytes(backup.size)}`);
          // 文件名放在提示里，不摆在正中间 —— 老师要认的是"哪一天的那份"，
          // 不是一串程序里的名字。
          label.title = backup.name;
          item.append(label, restore);
          list.append(item);
        });
        box.append(list);
        const total = megabytes(body.total_bytes || 0);
        // 1.26.26：这段以前把同一件事说了三遍（留几份、占多少、放在哪），
        // 老师看着只觉得绕。留几份在标题下那句话里已经说了，这里只报眼下有几份、多大。
        box.append(el("p", "settings-footnote", `一共 ${backups.length} 份，占 ${total}。旧的自动删掉。`));
      }
      // 1.26.22：自动备份开关 —— 用不上就关掉，随时能再打开。
      const toggle = el("label", "settings-switch");
      const input = el("input");
      input.type = "checkbox";
      input.checked = body.enabled !== false;
      input.addEventListener("change", async () => {
        input.disabled = true;
        try {
          await post("/api/settings/features", { features: { auto_backup: input.checked } });
          status("auto", input.checked
            ? `已打开：每天在本机打一份备份，留最近 ${keep} 份。`
            : "已关闭：以后不再自动打备份。想留一份可以点「现在备份一次」。", false);
        } catch (error) {
          input.checked = !input.checked;
          status("auto", error.message, true);
        } finally { input.disabled = false; }
      });
      toggle.append(input, el("span", "", "每天自动打一份备份"));
      box.append(toggle);
      box.append(el("p", "settings-footnote",
        "救的是「题库自己坏了」：软件出毛病、磁盘写坏时能救回来。"
        + "误删的题找回收站，原卷在你自己的硬盘上。"));
      const now = el("button", "button", "现在备份一次");
      now.type = "button";
      now.disabled = busy;
      now.addEventListener("click", async () => {
        if (busy) return;
        busy = true;
        now.disabled = true;
        status("auto", "正在打包，请稍候…");
        try {
          const result = await post("/api/backup/auto", {});
          status("auto", `已备份：${when(result.created_at)}，${megabytes(result.size)}。`, false);
          await loadAutoBackups();
        } catch (error) { status("auto", error.message, true); }
        finally { busy = false; }
      });
      box.append(now);
    } catch (error) {
      box.replaceChildren(el("p", "settings-footnote", "自动备份的状态暂时读不出来。"));
    }
  }

  async function restoreAutoBackup(id) {
    if (busy) return;
    busy = true;
    status("auto", "正在看这份备份会带来什么…");
    try {
      const preview = await post("/api/backup/auto/inspect", { id });
      const counts = ["papers", "questions", "tags", "publications"]
        .map((key) => ({ papers: "套卷子", questions: "道题", tags: "个标签", publications: "条入库题目" })[key] + " " + (preview[key] || 0))
        .join("、");
      status("auto", `这份备份里有 ${counts}。合并进来只会增加，本机原有的题一道不删。`, false);
      const go = el("button", "button primary", "合并进来");
      go.type = "button";
      go.addEventListener("click", async () => {
        go.disabled = true;
        status("auto", "正在合并…");
        try {
          const result = await post("/api/backup/auto/import", { id });
          status("auto", mergeReport(result.report), false);
          await loadAutoBackups();
          await loadRecords();
        } catch (error) { status("auto", error.message, true); }
        finally { go.disabled = false; busy = false; }
      });
      const box = $("transferAuto");
      if (box) box.append(go);
    } catch (error) {
      status("auto", error.message, true);
      busy = false;
    }
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
        item.append(el("span", "", `${when(record.created_at)}　并进来 ${bits.join("、") || "一些题目"}`));
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
    // 1.26.16：这一区的引导改由页头统一给（每个区一句），这里不再重复一遍。
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

    // 1.26.17：本机自动备份 —— 每天自己打一份，存在软件自己的文件夹里。
    // 1.26.21：改成每天一份；1.26.23：只留两份（今天和昨天），题库再大也只占两份。
    // 1.26.26：这段话精简 —— 老师 10 月 24 日说「介绍的太麻烦，简洁点，就是备份两次就可以了」。
    // 也不写「（今天和昨天）」：手动打过一次备份，两份就都是今天的；隔几天没开软件，
    // 留着的两份比昨天还旧。写死那句话就是承诺兑现不了的事。
    const autoBox = el("section", "settings-section");
    autoBox.append(el("h3", "", "本机自动备份"),
      el("p", "settings-footnote", "每天自动打一份备份，只留最近两份。放在本机，不联网、不上传。"));
    const autoInner = el("div");
    autoInner.id = "transferAuto";
    const autoStatus = el("p", "transfer-status");
    autoStatus.id = "transferStatusauto";
    autoBox.append(autoInner, autoStatus);

    host.append(autoBox, exportBox, mergeBox, recordsBox);
    renderExport(); renderMerge();
    void loadRecords();
    void loadAutoBackups();
  }

  window.LibraryTransfer = { mount, refresh: loadRecords };
})();
