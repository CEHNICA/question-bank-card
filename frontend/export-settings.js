(() => {
  "use strict";
  const API = "/api/export-preferences";
  let host = null, current = null, draft = null, busy = false, readFailed = false, unconfirmed = null;
  const $ = (id) => document.getElementById(id);
  const mode = () => draft?.mode || (current?.directory ? "folder" : "browser");
  const directory = () => draft?.directory ?? current?.directory ?? "";
  const destination = () => mode() === "browser" ? "" : directory();
  const changed = () => Boolean(current && draft && destination() !== current.directory);

  function controls() {
    const desktop = current?.desktop_capable === true, pending = changed();
    const enabled = desktop && !busy && !readFailed;
    $("exportDirectoryOptions").hidden = !desktop;
    $("exportDirectoryMode").value = mode();
    $("exportDirectoryMode").disabled = !enabled;
    $("exportDirectoryField").hidden = !desktop || mode() !== "folder";
    $("exportDirectory").value = directory();
    $("exportDirectory").disabled = !enabled;
    $("exportDirectorySelect").textContent = current?.directory ? "更改位置" : "选择文件夹";
    $("exportDirectorySelect").hidden = !desktop || pending || readFailed;
    $("exportDirectorySelect").disabled = !enabled;
    $("exportDirectoryOpen").hidden = !desktop || pending || readFailed || !current?.directory;
    $("exportDirectoryOpen").disabled = !enabled;
    $("exportDirectorySave").hidden = !desktop || !pending || readFailed;
    $("exportDirectorySave").disabled = !enabled;
    $("exportDirectoryCancel").hidden = !desktop || !pending || readFailed;
    $("exportDirectoryCancel").disabled = !enabled;
    $("exportDirectoryRetry").hidden = !readFailed;
    $("exportDirectoryRetry").disabled = busy;
    $("exportDirectoryRetry").textContent = unconfirmed === null ? "重试读取" : "确认保存结果";
  }
  function status(message) {
    $("exportDirectoryStatus").textContent = message || (current?.desktop_capable === false
      ? "网页版使用浏览器的下载设置。"
      : changed() ? mode() === "browser" ? "使用浏览器下载，保存后生效。" : "已选择新文件夹，保存后生效。"
      : current?.directory ? "Word 和 PDF 保存到这个文件夹，同名文件自动编号。" : "使用浏览器下载；也可以选择保存文件夹。");
  }
  async function request(url, payload, deadline = 15000) {
    const controller = new AbortController();
    // A person may take longer than a network deadline in a native file dialog.
    const timeout = deadline ? setTimeout(() => controller.abort(), deadline) : null;
    try {
      const response = await fetch(url, { method: payload === undefined ? "GET" : "POST", cache: "no-store", signal: controller.signal,
        headers: { "X-QB-Request": "1", ...(payload === undefined ? {} : { "Content-Type": "application/json" }) },
        ...(payload === undefined ? {} : { body: JSON.stringify(payload) }) });
      const body = await response.json();
      if (!response.ok) {
        const error = new Error(body.error || "导出位置设置未完成，请重试。");
        error.serverRejected = true;
        throw error;
      }
      return body;
    } catch (error) {
      if (error.name === "AbortError") throw new Error(payload === undefined ? "读取超时，请重试。" :
        url === API ? "保存结果尚未确认。" : "操作结果尚未确认，请重试。");
      throw error;
    } finally { if (timeout !== null) clearTimeout(timeout); }
  }
  function validate(body) {
    if (typeof body?.directory !== "string" || typeof body?.desktop_capable !== "boolean" ||
        (body.warning !== undefined && typeof body.warning !== "string")) throw new Error("设置读取不完整，请重试。");
    // A damaged preference returns a warning and an empty directory. That is
    // not proof that the user switched back to browser downloads.
    if (body.warning) throw new Error(body.warning);
    return body;
  }
  async function readSaved() {
    const body = validate(await request(API));
    current = body;
    if (draft && destination() === body.directory) draft = null;
    readFailed = false;
    const confirmed = unconfirmed !== null && body.directory === unconfirmed;
    const hadUnconfirmed = unconfirmed !== null;
    unconfirmed = null;
    status(confirmed ? "导出位置已保存。" : hadUnconfirmed && changed()
      ? "当前保存位置未变；所选位置仍保留，可以再次保存。" : undefined);
  }
  async function refresh() {
    if (!host || busy) return;
    busy = true; controls();
    try { await readSaved(); }
    catch (error) {
      readFailed = true;
      status((unconfirmed === null ? "" : "保存结果尚未确认。") + error.message);
    } finally { busy = false; controls(); }
  }
  async function save() {
    if (busy || readFailed || !current?.desktop_capable || !changed()) return;
    const selected = destination();
    busy = true; controls(); status("正在保存导出位置…");
    try {
      const body = validate(await request(API, { directory: selected }));
      if (body.directory !== selected) throw new Error("保存结果尚未确认。");
      current = body; draft = null; unconfirmed = null; readFailed = false;
      status("导出位置已保存。");
    } catch (error) {
      if (error.serverRejected) status(error.message);
      else {
        // A POST may have reached the server even when its reply was lost.
        // Read once before offering another save, retaining the user's draft.
        unconfirmed = selected;
        status("保存结果尚未确认，正在读取保存位置…");
        try { await readSaved(); }
        catch (readError) { readFailed = true; status("保存结果尚未确认。" + readError.message); }
      }
    } finally { busy = false; controls(); }
  }
  function cancel() {
    if (busy || readFailed || !current?.desktop_capable || !changed()) return;
    draft = null; status(); controls();
  }
  async function open() {
    if (busy || readFailed || changed() || !current?.desktop_capable || !current.directory) return;
    busy = true; controls();
    try { await request(`${API}/open`, { target: "directory" }); status("已打开导出文件夹。"); }
    catch (error) { status(error.message); }
    finally { busy = false; controls(); }
  }
  async function select() {
    if (busy || readFailed || changed() || !current?.desktop_capable) return;
    busy = true; controls(); status("请在系统窗口中选择文件夹，也可以取消。");
    try {
      const choice = await request(`${API}/select`, {}, 0);
      if (choice?.selected === false && choice?.cancelled === true) status();
      else if (choice?.selected === true && typeof choice.directory === "string" && choice.directory.trim()) {
        draft = { mode: "folder", directory: choice.directory };
        if (!changed()) draft = null;
        status();
      } else throw new Error("未能确认所选文件夹，请重新选择；已保存位置保持原样。");
    } catch (error) { status(error.message); }
    finally { busy = false; controls(); }
  }
  async function changeMode() {
    if (busy || readFailed || !current?.desktop_capable) { controls(); return; }
    const selected = $("exportDirectoryMode").value;
    if (selected === "browser") draft = { mode: "browser", directory: directory() };
    else if (selected === "folder") {
      if (directory()) draft = { mode: "folder", directory: directory() };
      else { await select(); return; }
    } else { controls(); return; }
    if (!changed()) draft = null;
    status(); controls();
  }
  async function mount(element) {
    if (!element || host) return;
    host = element;
    host.innerHTML = `<section class="settings-section" aria-labelledby="exportDirectoryTitle">
      <h3 id="exportDirectoryTitle">导出位置</h3>
      <div id="exportDirectoryOptions" hidden>
        <label class="field" for="exportDirectoryMode"><span>保存方式</span><select id="exportDirectoryMode" disabled>
          <option value="folder">指定文件夹</option><option value="browser">使用浏览器下载</option></select></label>
        <label id="exportDirectoryField" class="field" for="exportDirectory" hidden><span>保存文件夹</span>
          <input id="exportDirectory" type="text" readonly autocomplete="off" spellcheck="false" disabled></label>
      </div>
      <div class="settings-form-actions">
        <button id="exportDirectorySelect" type="button" class="button" hidden disabled>更改位置</button>
        <button id="exportDirectoryOpen" type="button" class="button" hidden disabled>打开文件夹</button>
        <button id="exportDirectorySave" type="button" class="button primary" hidden disabled>保存位置</button>
        <button id="exportDirectoryCancel" type="button" class="button" hidden disabled>取消</button>
        <button id="exportDirectoryRetry" type="button" class="button" hidden>重试读取</button>
      </div>
      <p id="exportDirectoryStatus" class="settings-footnote" role="status" aria-live="polite">正在读取…</p>
    </section>`;
    $("exportDirectorySave").addEventListener("click", () => { void save(); });
    $("exportDirectoryCancel").addEventListener("click", cancel);
    $("exportDirectorySelect").addEventListener("click", () => { void select(); });
    $("exportDirectoryMode").addEventListener("change", () => { void changeMode(); });
    $("exportDirectoryOpen").addEventListener("click", () => { void open(); });
    $("exportDirectoryRetry").addEventListener("click", () => { if (readFailed) void refresh(); });
    await refresh();
  }
  window.ExportSettings = Object.freeze({ mount, refresh });
})();
