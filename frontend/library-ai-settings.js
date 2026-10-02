(() => {
  "use strict";
  const API = "/api/settings/library-ai";
  const state = { current: null, dirty: false, busy: false, session: 0, inline: false, active: false, needsKeyReplacement: false };
  const defaults = {
    deepseek: { base_url: "https://api.deepseek.com", model: "deepseek-v4-pro", supports_images: false },
    doubao: { base_url: "https://ark.cn-beijing.volces.com/api/v3", model: "", supports_images: false },
    custom: { base_url: "", model: "", supports_images: false }
  };
  const fields = ["libraryAITags", "libraryAIAnswer", "libraryAITagsIntake", "libraryAIAnswerIntake", "libraryAIMode", "libraryAIProvider", "libraryAIBaseURL",
    "libraryAIModel", "libraryAIImages", "libraryAIThinking", "libraryAIKey", "libraryAIClearKey"];
  let dialog;
  const $ = (id) => document.getElementById(id);

  const isAPI = () => $("libraryAIMode").value === "api";
  const isActive = () => state.inline ? state.active : Boolean(dialog?.open);

  function create(host) {
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
      .library-ai-section input[type=text],.library-ai-section input[type=password],.library-ai-section select{width:100%;min-height:38px;padding:7px 10px;border:1px solid var(--line-strong);border-radius:9px;background:var(--surface);color:var(--ink)}
      .library-ai-switch{display:flex;gap:9px;align-items:flex-start;cursor:pointer}
      .library-ai-switch input{flex:none;margin-top:4px;accent-color:var(--accent)}
      .library-ai-switch span{display:grid;gap:2px}.library-ai-switch small{color:var(--muted)}
      .library-ai-timing{margin-left:25px}.library-ai-timing[hidden]{display:none}
      .library-ai-advanced>summary{cursor:pointer;font-size:13px;color:var(--ink-2);padding:4px 0}
      .library-ai-advanced[open]>summary{margin-bottom:10px}
      .library-ai-api-fields{display:grid;gap:9px}.library-ai-api-fields[hidden],.library-ai-section[hidden]{display:none}
      .library-ai-status{margin:0;padding:10px 12px;border-radius:10px;background:var(--accent-soft);font-size:13px;line-height:1.6}
      .library-ai-status.error{background:var(--amber-soft);color:var(--amber)}
      .library-ai-actions{display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end;padding:12px 20px;border-top:1px solid var(--line);background:var(--surface)}
      .library-ai-actions .settings-save-result{flex:1;min-width:120px}
      .library-ai-section .button{justify-self:start;max-width:100%;white-space:normal}
      .library-ai-panel{min-width:0}
      .library-ai-panel .dialog-head{padding:0 0 16px;border:0;background:none}
      .library-ai-panel .dialog-head h2{font-size:23px}
      .library-ai-panel .library-ai-body{padding:0;background:none;gap:24px;overflow:visible}
      .library-ai-panel .library-ai-section{border:0;padding:0;background:none;border-radius:0;gap:12px}
      .library-ai-panel .library-ai-section p,.library-ai-panel .library-ai-section label{font-size:14px}
      .library-ai-panel .library-ai-switch{padding:14px 0;border-bottom:1px solid var(--line);gap:12px}
      .library-ai-panel .library-ai-switch strong{font-size:15px}
      .library-ai-panel .library-ai-switch small{font-size:13px}
      .library-ai-panel .library-ai-timing{padding:0 0 10px;border:0;margin-left:28px}
      .library-ai-panel .library-ai-switch input{width:18px;height:18px;margin-top:3px}
      .library-ai-panel .library-ai-status{padding:10px 12px}
      .library-ai-panel .library-ai-actions{padding:18px 0 0;margin-top:24px;background:none}
      .library-ai-panel .library-ai-advanced>summary{font-size:14px}
      .library-ai-panel .library-ai-api-fields{padding:16px;border:1px solid var(--line);border-radius:10px;background:var(--surface-2)}
      .library-ai-panel .library-ai-api-fields .library-ai-switch{border:0;padding:4px 0}
      .library-ai-help>summary{cursor:pointer;font-size:13px;color:var(--muted)}
      .library-ai-help p{margin-top:8px}
      @media(max-width:480px){.library-ai-body{padding:12px}.library-ai-actions{padding:12px}.library-ai-dialog .dialog-head{padding:14px}}
    `;
    document.head.append(style);
    state.inline = Boolean(host);
    dialog = document.createElement(state.inline ? "section" : "dialog");
    dialog.id = "libraryAISettingsDialog";
    dialog.className = state.inline ? "library-ai-panel" : "library-ai-dialog";
    dialog.setAttribute("aria-labelledby", "libraryAISettingsTitle");
    dialog.innerHTML = `
      <form id="libraryAISettingsForm" class="library-ai-form">
        <div class="dialog-head"><h2 id="libraryAISettingsTitle">标签与答案</h2><button id="libraryAIClose" class="button quiet" type="button" aria-label="关闭标签与参考答案设置" ${state.inline ? "hidden" : ""}>关闭</button></div>
        <div class="library-ai-body">
          <p id="libraryAIState" class="library-ai-status" role="status" aria-live="polite">正在读取本机设置…</p>
          <section class="library-ai-section" aria-label="分别开启功能">
            <p>两项默认关闭。开启后，可在题库单题生成或勾选批量生成。</p>
            <label class="library-ai-switch"><input id="libraryAITags" type="checkbox"><span><strong>生成知识点标签</strong><small>从知识点目录选标签，方便下次找题。</small></span></label>
            <label id="libraryAITagsTiming" class="library-ai-switch library-ai-timing" hidden><input id="libraryAITagsIntake" type="checkbox"><span>新题入库时生成标签</span></label>
            <label class="library-ai-switch"><input id="libraryAIAnswer" type="checkbox"><span><strong>补充 AI 参考答案</strong><small>原卷无答案时补充解答，保存为“AI 参考 · 未核对”。</small></span></label>
            <label id="libraryAIAnswerTiming" class="library-ai-switch library-ai-timing" hidden><input id="libraryAIAnswerIntake" type="checkbox"><span>新题入库时生成参考答案</span></label>
          </section>
          <section class="library-ai-section" aria-label="生成方式">
            <label for="libraryAIMode"><strong>由谁生成</strong></label>
            <select id="libraryAIMode"><option value="assistant">当前 AI 助手（默认）</option><option value="api">独立模型 API</option></select>
          </section>
          <section id="libraryAIAssistantHelp" class="library-ai-section" aria-label="当前 AI 助手处理">
            <p>由正在帮你操作的豆包或其他助手完成并交回，无需填写豆包 API。</p>
            <details class="library-ai-help"><summary>助手如何处理待办？</summary><p>新题入库时生成会创建待办。当前助手需支持本机工具，并实际领取任务、生成结果、写回题库；网页按钮不会自动唤醒桌面豆包。</p></details>
          </section>
          <details id="libraryAIAdvanced" class="library-ai-advanced">
            <summary>独立模型配置 · 推荐 DeepSeek，也支持其他模型</summary>
            <section class="library-ai-section" aria-label="可选处理方式">
              <div id="libraryAIAPIFields" class="library-ai-api-fields" hidden>
                <p>推荐 DeepSeek Pro，也可配置豆包或其他兼容服务。模型名以服务商实际提供的 ID 为准，与读题服务分开保存。</p>
                <label for="libraryAIProvider">服务商</label><select id="libraryAIProvider"><option value="deepseek">DeepSeek（推荐）</option><option value="doubao">豆包 API</option><option value="custom">其他兼容服务</option></select>
                <label for="libraryAIBaseURL">API 地址</label><input id="libraryAIBaseURL" type="text" placeholder="https://api.deepseek.com" autocomplete="off" spellcheck="false">
                <label for="libraryAIModel">模型 ID</label><input id="libraryAIModel" type="text" placeholder="服务商提供的模型 ID；豆包填写 Endpoint ID" autocomplete="off" spellcheck="false">
                <label class="library-ai-switch"><input id="libraryAIImages" type="checkbox"><span>此模型支持图片<small>仅在服务商确认支持时开启；纯文本模型不会跳过配图处理含图题。</small></span></label>
                <label class="library-ai-switch"><input id="libraryAIThinking" type="checkbox"><span>开启数学思考</span></label>
                <label for="libraryAIKey">API Key · 已保存的密钥不会回显</label><input id="libraryAIKey" type="password" autocomplete="new-password" placeholder="留空保留当前服务商的密钥" spellcheck="false">
                <label class="library-ai-switch"><input id="libraryAIClearKey" type="checkbox"><span>清除已保存的 API Key</span></label>
                <p>密钥用当前 Windows 用户加密保存。保存不联网；更换服务商后请使用对应的密钥。</p>
                <strong>先确认能力，再生成</strong><p>保存后，用软件自带的合成题测试服务响应及所选的图像、思考能力，不上传你的试卷。测试不评定数学水平；生成准确性仍需核对，测试与生成可能产生 API 费用。</p>
                <label class="library-ai-switch"><input id="libraryAITestConsent" type="checkbox"><span>我确认发起一次可能计费的 API 测试</span></label>
                <button id="libraryAITest" class="button" type="button" disabled>测试连接</button>
              </div>
            </section>
          </details>
        </div>
        <div class="library-ai-actions"><span id="libraryAIResult" class="settings-save-result" role="status" aria-live="polite"></span><button id="libraryAICancel" class="button" type="button">${state.inline ? "撤销更改" : "取消"}</button><button id="libraryAISave" class="button primary" type="submit" disabled>保存设置</button></div>
      </form>`;
    (host || document.body).append(dialog);
    $("libraryAISettingsForm").addEventListener("submit", (event) => { event.preventDefault(); void save(); });
    for (const id of fields) {
      const changed = () => {
        state.dirty = true;
        if (id === "libraryAIClearKey") {
          $("libraryAIKey").disabled = $(id).checked;
          if ($(id).checked) { $("libraryAIKey").value = ""; state.needsKeyReplacement = false; }
        }
        if (id === "libraryAIKey" && $(id).value.trim()) state.needsKeyReplacement = false;
        if (id === "libraryAIMode") { clearSecret(); renderMode(); }
        if (["libraryAITags", "libraryAIAnswer"].includes(id)) renderTiming();
        if (id === "libraryAIProvider") {
          const preset = defaults[$(id).value];
          if (preset) {
            $("libraryAIBaseURL").value = preset.base_url;
            $("libraryAIModel").value = preset.model;
            $("libraryAIImages").checked = preset.supports_images;
            $("libraryAIThinking").checked = true;
          }
          clearSecret();
        }
        $("libraryAIResult").textContent = id === "libraryAIProvider" ? "服务商已切换，请使用对应的密钥，再保存。" :
          state.needsKeyReplacement ? "新密钥未保存，请重新填写 API Key 后再保存。" :
            isAPI() ? "有未保存的设置；保存后再测试。" : "有未保存的设置。";
        updateButtons();
      };
      $(id).addEventListener("input", changed);
      if (["libraryAIMode", "libraryAIProvider"].includes(id)) $(id).addEventListener("change", changed);
    }
    $("libraryAITestConsent").addEventListener("change", updateButtons);
    $("libraryAITest").addEventListener("click", () => { void test(); });
    $("libraryAICancel").addEventListener("click", close);
    $("libraryAIClose").addEventListener("click", close);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
    dialog.addEventListener("close", () => { state.session++; clearSecret(); state.dirty = false; state.busy = false; });
  }

  function clearSecret() {
    state.needsKeyReplacement = false;
    $("libraryAIKey").value = "";
    $("libraryAIClearKey").checked = false;
    $("libraryAIKey").disabled = false;
    $("libraryAITestConsent").checked = false;
  }

  function renderMode() {
    $("libraryAIAPIFields").hidden = !isAPI();
    $("libraryAIAssistantHelp").hidden = isAPI();
    $("libraryAIAdvanced").hidden = !isAPI();
    if (isAPI()) $("libraryAIAdvanced").open = true;
    $("libraryAISave").textContent = isAPI() ? "加密保存设置" : "保存设置";
  }

  function renderTiming() {
    $("libraryAITagsTiming").hidden = !$("libraryAITags").checked;
    $("libraryAIAnswerTiming").hidden = !$("libraryAIAnswer").checked;
  }

  function updateButtons() {
    $("libraryAISave").disabled = state.busy || !state.current || (isAPI() && state.needsKeyReplacement);
    $("libraryAITest").disabled = state.busy || state.dirty || !isAPI() || state.current?.mode !== "api" || !state.current?.configured || !$("libraryAITestConsent").checked;
    for (const id of fields) $(id).disabled = state.busy || !state.current;
    $("libraryAITagsIntake").disabled = state.busy || !state.current || !$("libraryAITags").checked;
    $("libraryAIAnswerIntake").disabled = state.busy || !state.current || !$("libraryAIAnswer").checked;
    $("libraryAITestConsent").disabled = state.busy || !state.current || !isAPI();
    $("libraryAIKey").disabled = state.busy || !state.current || $("libraryAIClearKey").checked;
    if (state.inline) {
      $("libraryAICancel").disabled = state.busy;
      $("libraryAICancel").textContent = state.current ? "撤销更改" : "重新读取";
    }
  }

  async function request(url, payload) {
    const response = await fetch(url, payload === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" }, body: JSON.stringify(payload)
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.error || "设置未完成，请重试。");
    if (!body || !["assistant", "api"].includes(body.mode) || !Object.hasOwn(defaults, body.provider)
        || typeof body.configured !== "boolean" || typeof body.ready !== "boolean"
        || typeof body.base_url !== "string" || typeof body.model !== "string"
        || typeof body.supports_images !== "boolean" || typeof body.thinking !== "boolean"
        || typeof body.on_intake?.tags !== "boolean" || typeof body.on_intake?.answer !== "boolean"
        || typeof body.features?.knowledge_tags !== "boolean" || typeof body.features?.ai_answer !== "boolean") {
      throw new Error("设置状态读取不完整，请重新读取设置后再试。");
    }
    return body;
  }

  function render(body) {
    state.current = body;
    state.dirty = false;
    $("libraryAITags").checked = body.features.knowledge_tags;
    $("libraryAIAnswer").checked = body.features.ai_answer;
    $("libraryAITagsIntake").checked = body.on_intake.tags;
    $("libraryAIAnswerIntake").checked = body.on_intake.answer;
    renderTiming();
    $("libraryAIMode").value = body.mode;
    $("libraryAIProvider").value = body.provider;
    $("libraryAIBaseURL").value = body.base_url || "";
    $("libraryAIModel").value = body.model || "";
    $("libraryAIImages").checked = body.supports_images === true;
    $("libraryAIThinking").checked = body.thinking !== false;
    $("libraryAIAdvanced").open = body.mode === "api" || $("libraryAIAdvanced").open;
    renderMode();
    $("libraryAIState").textContent = body.mode === "assistant" ? "当前 AI 助手处理 · 无需额外豆包 API" :
      body.message || (body.ready ? "独立 API 已通过测试，结果仍需核对。" : "请在下方配置并显式测试独立 API。");
    $("libraryAIState").classList.toggle("error", body.mode === "api" && !body.ready);
  }

  async function open() {
    create();
    if (state.inline) { window.location.hash = "ai"; return; }
    if (isActive()) return;
    dialog.showModal();
    await load();
  }

  async function mount(host) {
    if (!host) return;
    create(host);
    if (!state.inline || state.active) return;
    state.active = true;
    await load();
  }

  async function load() {
    const session = ++state.session;
    state.current = null; state.dirty = false; state.busy = true;
    clearSecret();
    $("libraryAIAdvanced").open = false;
    $("libraryAITagsTiming").hidden = true;
    $("libraryAIAnswerTiming").hidden = true;
    $("libraryAIAPIFields").hidden = true;
    $("libraryAIAssistantHelp").hidden = false;
    $("libraryAIResult").textContent = "";
    $("libraryAIState").textContent = "正在读取本机设置…";
    updateButtons();
    try {
      const body = await request(API);
      if (session !== state.session || !isActive()) return;
      render(body);
    } catch (error) {
      if (session === state.session && isActive()) $("libraryAIState").textContent = error.message;
    } finally {
      if (session === state.session && isActive()) { state.busy = false; updateButtons(); }
    }
  }

  function discard() {
    if (state.busy) return false;
    clearSecret();
    if (state.current) {
      render(state.current);
      $("libraryAIResult").textContent = "已恢复保存的设置。";
      updateButtons();
    } else {
      void load();
    }
    return true;
  }

  function close() {
    if (state.inline) { discard(); return; }
    if (state.dirty && !window.confirm("这些设置还没保存。放弃更改并关闭？")) return;
    dialog.close();
  }

  async function save() {
    if (state.busy || !state.current) return;
    if (isAPI() && state.needsKeyReplacement) {
      $("libraryAIResult").textContent = "新密钥未保存，请重新填写 API Key 后再保存。";
      return;
    }
    const session = state.session;
    const payload = { mode: $("libraryAIMode").value,
      features: { knowledge_tags: $("libraryAITags").checked, ai_answer: $("libraryAIAnswer").checked },
      on_intake: { tags: $("libraryAITagsIntake").checked, answer: $("libraryAIAnswerIntake").checked } };
    let key, failedKeyAction;
    if (isAPI()) {
      const value = $("libraryAIKey").value.trim();
      key = $("libraryAIClearKey").checked ? { action: "clear" } : value ? { action: "replace", value } : { action: "keep" };
      Object.assign(payload, { provider: $("libraryAIProvider").value, base_url: $("libraryAIBaseURL").value.trim(),
        model: $("libraryAIModel").value.trim(), supports_images: $("libraryAIImages").checked,
        thinking: $("libraryAIThinking").checked, reasoning_effort: "high", key });
    }
    state.busy = true; updateButtons();
    $("libraryAIResult").textContent = payload.mode === "api" ? "正在加密保存…" : "正在保存设置…";
    try {
      const body = await request(API, payload);
      document.dispatchEvent(new CustomEvent("library-ai-settings-saved", { detail: body }));
      if (session !== state.session || !isActive()) return;
      render(body);
      $("libraryAIResult").textContent = body.mode === "assistant" ? "已保存；请把题目交给当前助手处理并写回。" :
        body.ready ? "已保存，生成按这两个开关分别执行。" : "已保存；请显式测试后再生成。";
    } catch (error) {
      failedKeyAction = key?.action;
      if (session === state.session && isActive()) $("libraryAIResult").textContent = error.message +
        (failedKeyAction === "replace" ? "。新密钥未保存，请重新填写 API Key 后再保存。" : "");
    } finally {
      if (key) delete key.value;
      if (session === state.session && isActive()) {
        clearSecret();
        state.needsKeyReplacement = failedKeyAction === "replace";
        $("libraryAIClearKey").checked = failedKeyAction === "clear";
        state.busy = false; updateButtons();
      }
    }
  }

  async function test() {
    if (state.busy || state.dirty || !isAPI() || state.current?.mode !== "api" || !state.current?.configured || !$("libraryAITestConsent").checked) return;
    const session = state.session;
    state.busy = true; updateButtons();
    $("libraryAIResult").textContent = "正在用合成题测试一次，请稍候…";
    try {
      const body = await request(`${API}/test`, { confirm: true });
      if (session !== state.session || !isActive()) return;
      render(body);
      $("libraryAIResult").textContent = "连接测试已通过；生成内容仍需人工核对。";
    } catch (error) {
      if (session === state.session && isActive()) {
        $("libraryAIResult").textContent = error.message;
        try { const body = await request(API); if (session === state.session && isActive()) render(body); } catch { /* 保留实际错误 */ }
      }
    } finally {
      if (session === state.session && isActive()) { $("libraryAITestConsent").checked = false; state.busy = false; updateButtons(); }
    }
  }

  window.LibraryAISettings = Object.freeze({ open, mount, discard,
    hasUnsavedChanges: () => state.dirty, isBusy: () => state.busy });
  window.addEventListener("beforeunload", (event) => {
    if (!state.inline || !isActive() || (!state.dirty && !state.busy)) return;
    event.preventDefault();
    event.returnValue = "";
  });
  document.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-library-ai-settings]")) { event.preventDefault(); void open(); }
  });
})();
