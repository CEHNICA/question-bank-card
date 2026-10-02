(() => {
  "use strict";
  const API = "/api/settings/library-ai";
  const state = { current: null, dirty: false, busy: false, session: 0 };
  let dialog;
  const $ = (id) => document.getElementById(id);

  function create() {
    if (dialog) return;
    const style = document.createElement("style");
    style.textContent = `
      .library-ai-dialog{width:min(690px,94vw);max-height:92vh;max-height:92dvh;padding:0;overflow:hidden}
      .library-ai-dialog[open]{display:flex;flex-direction:column}
      .library-ai-form{display:flex;flex-direction:column;min-height:0}
      .library-ai-body{flex:1;padding:16px 20px;overflow:auto;min-height:0;display:grid;gap:12px;background:var(--bg)}
      .library-ai-form .dialog-head,.library-ai-actions{flex:none}
      .library-ai-section{display:grid;gap:9px;padding:13px;border:1px solid var(--line);border-radius:12px;background:var(--surface)}
      .library-ai-section p{margin:0;font-size:13px;line-height:1.6;color:var(--ink-2)}
      .library-ai-section label{font-size:13px;line-height:1.5}
      .library-ai-section input[type=text],.library-ai-section input[type=password]{width:100%;min-height:38px;padding:7px 10px;border:1px solid var(--line-strong);border-radius:9px;background:var(--surface);color:var(--ink)}
      .library-ai-switch{display:flex;gap:9px;align-items:flex-start;cursor:pointer}
      .library-ai-switch input{flex:none;margin-top:4px;accent-color:var(--accent)}
      .library-ai-switch span{display:grid;gap:2px}.library-ai-switch small{color:var(--muted)}
      .library-ai-status{padding:10px 12px;border-radius:10px;background:var(--accent-soft);font-size:13px;line-height:1.6}
      .library-ai-status.error{background:var(--amber-soft);color:var(--amber)}
      .library-ai-actions{display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end;padding:12px 20px;border-top:1px solid var(--line);background:var(--surface)}
      .library-ai-actions .settings-save-result{flex:1;min-width:120px}
      .library-ai-section .button{justify-self:start;max-width:100%;white-space:normal}
      @media(max-width:480px){.library-ai-body{padding:12px}.library-ai-actions{padding:12px}.library-ai-dialog .dialog-head{padding:14px}}
    `;
    document.head.append(style);
    dialog = document.createElement("dialog");
    dialog.id = "libraryAISettingsDialog";
    dialog.className = "library-ai-dialog";
    dialog.setAttribute("aria-labelledby", "libraryAISettingsTitle");
    dialog.innerHTML = `
      <form id="libraryAISettingsForm" class="library-ai-form">
        <div class="dialog-head"><h2 id="libraryAISettingsTitle">标签与参考答案设置</h2><button id="libraryAIClose" class="button quiet" type="button" aria-label="关闭标签与参考答案设置">关闭</button></div>
        <div class="library-ai-body">
          <p id="libraryAIState" class="library-ai-status" role="status" aria-live="polite">正在读取本机设置…</p>
          <section class="library-ai-section" aria-label="分别开启功能">
            <label class="library-ai-switch"><input id="libraryAITags" type="checkbox"><span><strong>知识点标签</strong><small>从固定目录选择标签，方便找题；默认关闭。</small></span></label>
            <label class="library-ai-switch"><input id="libraryAIAnswer" type="checkbox"><span><strong>AI 参考答案</strong><small>单独标记“AI 参考 · 未核对”，不会替换原卷答案；默认关闭。</small></span></label>
          </section>
          <section class="library-ai-section" aria-label="独立豆包 API 配置">
            <strong>独立豆包 Pro API · 数学思考已开启</strong>
            <p>在火山方舟选择支持图文与思考的豆包 Pro 强模型，填写其 Endpoint ID。安装桌面豆包或连接其他 AI 助手，不代表这里已连接。</p>
            <label for="libraryAIEndpoint">模型 Endpoint ID</label><input id="libraryAIEndpoint" type="text" placeholder="从火山方舟控制台复制 ep- 开头的 ID" autocomplete="off" spellcheck="false">
            <label for="libraryAIKey">豆包 API Key · 已保存的密钥不会回显</label><input id="libraryAIKey" type="password" autocomplete="new-password" placeholder="留空保持已保存的密钥" spellcheck="false">
            <label class="library-ai-switch"><input id="libraryAIClearKey" type="checkbox"><span>清除已保存的豆包 API Key</span></label>
            <p>密钥仅用当前 Windows 用户加密保存，与 OCR 密钥分开。保存只检查格式，不联网、不测试模型。</p>
            <p>没有可用豆包时暂停生成；可考虑 DeepSeek Pro。软件不会自动转用它、OCR 读题模型或其他 AI 助手。</p>
          </section>
          <section class="library-ai-section" aria-label="显式连接测试">
            <strong>确认能力后再生成</strong><p>先保存，再用软件自带的合成数学题与图测试一次。不会上传你的试卷；测试和生成可能产生 API 费用。</p>
            <label class="library-ai-switch"><input id="libraryAITestConsent" type="checkbox"><span>我确认发起一次可能计费的豆包 API 测试</span></label>
            <button id="libraryAITest" class="button" type="button" disabled>测试图文与思考能力</button>
          </section>
        </div>
        <div class="library-ai-actions"><span id="libraryAIResult" class="settings-save-result" role="status" aria-live="polite"></span><button id="libraryAICancel" class="button" type="button">取消</button><button id="libraryAISave" class="button primary" type="submit" disabled>加密保存设置</button></div>
      </form>`;
    document.body.append(dialog);
    $("libraryAISettingsForm").addEventListener("submit", (event) => { event.preventDefault(); void save(); });
    for (const id of ["libraryAITags", "libraryAIAnswer", "libraryAIEndpoint", "libraryAIKey", "libraryAIClearKey"]) {
      $(id).addEventListener("input", () => {
        state.dirty = true;
        if (id === "libraryAIClearKey") {
          $("libraryAIKey").disabled = $(id).checked;
          if ($(id).checked) $("libraryAIKey").value = "";
        }
        $("libraryAIResult").textContent = "有未保存的设置；保存后再测试。";
        updateButtons();
      });
    }
    $("libraryAITestConsent").addEventListener("change", updateButtons);
    $("libraryAITest").addEventListener("click", () => { void test(); });
    $("libraryAICancel").addEventListener("click", close);
    $("libraryAIClose").addEventListener("click", close);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
    dialog.addEventListener("close", () => { state.session++; clearSecret(); state.dirty = false; state.busy = false; });
  }

  function clearSecret() {
    $("libraryAIKey").value = "";
    $("libraryAIClearKey").checked = false;
    $("libraryAIKey").disabled = false;
    $("libraryAITestConsent").checked = false;
  }

  function updateButtons() {
    $("libraryAISave").disabled = state.busy || !state.current;
    $("libraryAITest").disabled = state.busy || state.dirty || !state.current?.configured || !$("libraryAITestConsent").checked;
    for (const id of ["libraryAITags", "libraryAIAnswer", "libraryAIEndpoint", "libraryAIClearKey", "libraryAITestConsent"]) $(id).disabled = state.busy || !state.current;
    $("libraryAIKey").disabled = state.busy || !state.current || $("libraryAIClearKey").checked;
  }

  async function request(url, payload) {
    const response = await fetch(url, payload === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" }, body: JSON.stringify(payload)
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "设置未完成，请重试。");
    if (!body || typeof body.configured !== "boolean" || typeof body.ready !== "boolean"
        || typeof body.features?.knowledge_tags !== "boolean" || typeof body.features?.ai_answer !== "boolean") {
      throw new Error("设置状态读取不完整，请关闭后重新打开。");
    }
    return body;
  }

  function render(body) {
    state.current = body;
    state.dirty = false;
    $("libraryAITags").checked = body.features.knowledge_tags;
    $("libraryAIAnswer").checked = body.features.ai_answer;
    $("libraryAIEndpoint").value = body.endpoint_id || "";
    $("libraryAIState").textContent = (body.message || (body.ready ? "豆包 API 已通过测试。" : "生成暂停，请配置并显式测试豆包 API。"))
      .replace("请打开“标签与参考答案设置”", "请在下方完成配置并测试");
    $("libraryAIState").classList.toggle("error", !body.ready);
  }

  async function open() {
    create();
    if (dialog.open) return;
    const session = ++state.session;
    state.current = null; state.dirty = false; state.busy = true;
    clearSecret();
    $("libraryAIResult").textContent = "";
    $("libraryAIState").textContent = "正在读取本机设置…";
    updateButtons();
    dialog.showModal();
    try {
      const body = await request(API);
      if (session !== state.session || !dialog.open) return;
      render(body);
    } catch (error) {
      if (session === state.session && dialog.open) $("libraryAIState").textContent = error.message;
    } finally {
      if (session === state.session && dialog.open) { state.busy = false; updateButtons(); }
    }
  }

  function close() {
    if (state.dirty && !window.confirm("这些设置还没保存。放弃更改并关闭？")) return;
    dialog.close();
  }

  async function save() {
    if (state.busy || !state.current) return;
    const session = state.session;
    const value = $("libraryAIKey").value.trim();
    const key = $("libraryAIClearKey").checked ? { action: "clear" } : value ? { action: "replace", value } : { action: "keep" };
    const payload = { features: { knowledge_tags: $("libraryAITags").checked, ai_answer: $("libraryAIAnswer").checked },
      endpoint_id: $("libraryAIEndpoint").value.trim(), thinking: true, key };
    state.busy = true; updateButtons();
    $("libraryAIResult").textContent = "正在加密保存…";
    try {
      const body = await request(API, payload);
      document.dispatchEvent(new CustomEvent("library-ai-settings-saved", { detail: body }));
      if (session !== state.session || !dialog.open) return;
      render(body);
      $("libraryAIResult").textContent = body.ready ? "已保存，生成按这两个开关分别执行。" : "已保存；尚未核验，请显式测试后再生成。";
    } catch (error) {
      if (session === state.session && dialog.open) $("libraryAIResult").textContent = error.message;
    } finally {
      delete key.value;
      if (session === state.session && dialog.open) { clearSecret(); state.busy = false; updateButtons(); }
    }
  }

  async function test() {
    if (state.busy || state.dirty || !state.current?.configured || !$("libraryAITestConsent").checked) return;
    const session = state.session;
    state.busy = true; updateButtons();
    $("libraryAIResult").textContent = "正在用合成图测试一次，请稍候…";
    try {
      const body = await request(`${API}/test`, { confirm: true });
      if (session !== state.session || !dialog.open) return;
      render(body);
      $("libraryAIResult").textContent = "图文与思考测试已通过；数学答案仍需人工核对。";
    } catch (error) {
      if (session === state.session && dialog.open) {
        $("libraryAIResult").textContent = error.message;
        try { const body = await request(API); if (session === state.session && dialog.open) render(body); } catch { /* 保留实际错误 */ }
      }
    } finally {
      if (session === state.session && dialog.open) { $("libraryAITestConsent").checked = false; state.busy = false; updateButtons(); }
    }
  }

  window.LibraryAISettings = Object.freeze({ open });
  document.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-library-ai-settings]")) { event.preventDefault(); void open(); }
  });
})();
