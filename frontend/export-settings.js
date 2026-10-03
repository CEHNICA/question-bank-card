(() => {
  "use strict";
  const API = "/api/export-preferences";
  let host = null, current = null, busy = false;
  const $ = (id) => document.getElementById(id);
  function controls() {
    const enabled = current?.desktop_capable === true && !busy;
    $("exportDirectory").disabled = !enabled;
    $("exportDirectorySave").disabled = !enabled;
    $("exportDirectorySelect").disabled = !enabled;
    $("exportDirectoryReset").disabled = !enabled;
    $("exportDirectoryOpen").disabled = !enabled || !current?.directory;
    $("exportDirectoryRetry").hidden = false;
    $("exportDirectoryRetry").disabled = busy;
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
      if (!response.ok) throw new Error(body.error || "导出位置设置未完成，请重试。");
      return body;
    } catch (error) { if (error.name === "AbortError") throw new Error(payload === undefined ? "读取超时，请重新读取。" : "操作结果尚未确认，请重新读取保存位置后确认。"); throw error; }
    finally { if (timeout !== null) clearTimeout(timeout); }
  }
  function render(body) {
    if (typeof body?.directory !== "string" || typeof body?.desktop_capable !== "boolean") throw new Error("设置读取不完整，请重新读取。");
    current = body;
    $("exportDirectory").value = body.directory;
    $("exportDirectoryStatus").textContent = body.warning || (body.desktop_capable ?
      body.directory ? "Word 和 PDF 将保存到这个文件夹，同名文件自动编号。" : "目前使用浏览器下载。点击“选择文件夹”选定位置后，再保存。" :
      "网页版使用浏览器的下载设置；桌面版可以在这里指定导出文件夹。");
  }
  async function load() {
    if (busy) return;
    busy = true; controls();
    try { render(await request(API)); }
    catch (error) { $("exportDirectoryStatus").textContent = error.message; }
    finally { busy = false; controls(); }
  }
  async function save(reset = false) {
    if (busy || !current?.desktop_capable) return;
    const directory = reset ? "" : $("exportDirectory").value.trim();
    busy = true; controls();
    $("exportDirectoryStatus").textContent = "正在保存导出位置…";
    try { render(await request(API, { directory })); }
    catch (error) { $("exportDirectoryStatus").textContent = error.message; }
    finally { busy = false; controls(); }
  }
  async function open() {
    if (busy || !current?.desktop_capable || !current.directory) return;
    busy = true; controls();
    try { await request(`${API}/open`, { target: "directory" }); $("exportDirectoryStatus").textContent = "已打开保存的导出文件夹。"; }
    catch (error) { $("exportDirectoryStatus").textContent = error.message; }
    finally { busy = false; controls(); }
  }
  async function select() {
    if (busy || !current?.desktop_capable) return;
    busy = true; controls();
    $("exportDirectoryStatus").textContent = "请在系统窗口中选择文件夹，也可以取消。";
    try {
      const choice = await request(`${API}/select`, {}, 0);
      if (choice?.selected === false && choice?.cancelled === true) {
        $("exportDirectoryStatus").textContent = "已取消选择，导出位置保持原样。";
      } else if (choice?.selected === true && typeof choice.directory === "string" && choice.directory.trim()) {
        $("exportDirectory").value = choice.directory;
        $("exportDirectoryStatus").textContent = "已选择文件夹，点击“保存位置”后生效。";
      } else throw new Error("未能确认所选文件夹，请重新选择；已保存位置保持原样。");
    } catch (error) { $("exportDirectoryStatus").textContent = error.message; }
    finally { busy = false; controls(); }
  }
  async function mount(element) {
    if (!element || host) return;
    host = element;
    host.innerHTML = `<section class="settings-section" aria-labelledby="exportDirectoryTitle">
      <h3 id="exportDirectoryTitle">导出位置</h3>
      <p class="settings-footnote">桌面版可选择 Word 与 PDF 的保存文件夹。同名文件自动编号；也可使用浏览器下载。</p>
      <label class="field" for="exportDirectory"><span>保存文件夹</span><input id="exportDirectory" type="text" autocomplete="off" spellcheck="false" placeholder="如 D:\\试卷导出" disabled></label>
      <div class="settings-form-actions"><button id="exportDirectorySelect" type="button" class="button" disabled>选择文件夹</button>
        <button id="exportDirectorySave" type="button" class="button primary" disabled>保存位置</button>
        <button id="exportDirectoryReset" type="button" class="button" disabled>使用浏览器下载</button>
        <button id="exportDirectoryOpen" type="button" class="button" disabled>打开已保存文件夹</button>
        <button id="exportDirectoryRetry" type="button" class="button">读取已保存位置</button></div>
      <p id="exportDirectoryStatus" class="settings-footnote" role="status" aria-live="polite">正在读取…</p>
    </section>`;
    $("exportDirectorySave").addEventListener("click", () => { void save(); });
    $("exportDirectorySelect").addEventListener("click", () => { void select(); });
    $("exportDirectoryReset").addEventListener("click", () => { void save(true); });
    $("exportDirectoryOpen").addEventListener("click", () => { void open(); });
    $("exportDirectoryRetry").addEventListener("click", () => { void load(); });
    await load();
  }
  window.ExportSettings = Object.freeze({ mount });
})();
