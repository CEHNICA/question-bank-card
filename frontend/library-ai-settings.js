(() => {
  "use strict";
  const API = "/api/settings/library-ai";
  const state = { current: null, baseline: null, provider: "", dirty: false, busy: false, operation: null, session: 0, inline: false, embedded: false, active: false, needsKeyReplacement: false, reading: {} };
  const defaults = {
    deepseek: { base_url: "https://api.deepseek.com", model: "deepseek-v4-pro", supports_images: false },
    doubao: { base_url: "https://ark.cn-beijing.volces.com/api/v3", model: "", supports_images: false },
    minimax: { base_url: "https://api.minimax.cn/v1", model: "MiniMax-M3.1-Flash-Preview", supports_images: true },
    custom: { base_url: "", model: "", supports_images: false }
  };
  const providerNames = { deepseek: "DeepSeek", minimax: "MiniMax", doubao: "豆包", custom: "其他兼容服务" };
  const eyeIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
  const fields = ["libraryAITags", "libraryAIAnswer", "libraryAITagsIntake", "libraryAIAnswerIntake", "libraryAIMode", "libraryAIProvider", "libraryAIBaseURL",
    "libraryAIModel", "libraryAIImages", "libraryAIThinking", "libraryAIKey", "libraryAIClearKey"];
  let dialog;
  const requests = new Map();
  let revealEpoch = 0, revealController = null, revealTimer = null, revealedProvider = null, revealing = false;
  const $ = (id) => document.getElementById(id);

  const isAPI = () => true;
  const isActive = () => state.inline ? state.active : Boolean(dialog?.open);

  function create(host, { embedded = false } = {}) {
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
      .library-ai-switch{display:inline-flex;width:fit-content;max-width:100%;justify-self:start;gap:9px;align-items:flex-start;cursor:pointer}
      .library-ai-switch input{flex:none;margin-top:4px;accent-color:var(--accent)}
      .library-ai-switch span{display:grid;min-width:0;gap:2px}.library-ai-switch small{color:var(--muted)}
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
      .library-ai-key{display:flex;gap:8px;align-items:center}.library-ai-key input{min-width:0;flex:1}
      .library-ai-key .button{flex:none;padding:9px 12px;min-height:38px}.library-ai-key svg{width:20px;height:20px;display:block;fill:none;stroke:currentColor;stroke-width:1.7}
      .library-ai-share{display:grid;gap:8px;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:var(--accent-soft)}
      .library-ai-share[hidden]{display:none}
      .library-ai-share p{margin:0}
      .library-ai-share-actions{display:flex;flex-wrap:wrap;gap:8px}
      .library-ai-stored-keys{display:grid;gap:8px}.library-ai-stored-key{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:6px 10px;align-items:center}      .library-ai-stored-key>span{font-size:12px;color:var(--ink-2)}.library-ai-stored-key>input{grid-column:1/-1}
      .library-ai-stored-key>.button{min-height:34px;padding:5px 9px;justify-self:end;font-size:12px}
      .library-ai-stored-key>.button svg{width:20px;height:20px;display:block;fill:none;stroke:currentColor;stroke-width:1.7}
      .library-ai-stored-key>.button[aria-pressed=true]{border-color:var(--accent);background:var(--accent-soft)}
      @media(max-width:480px){.library-ai-body{padding:12px}.library-ai-actions{padding:12px}.library-ai-dialog .dialog-head{padding:14px}}
    `;
    document.head.append(style);
    state.inline = Boolean(host);
    state.embedded = embedded;
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
          <section class="library-ai-section" aria-label="生成方式" hidden>
            <input id="libraryAIMode" type="hidden" value="api">
          </section>
          <section id="libraryAIAssistantHelp" class="library-ai-section" hidden></section>
          <details id="libraryAIAdvanced" class="library-ai-advanced">
            <summary>独立模型配置 · DeepSeek、MiniMax、豆包及其他模型</summary>
            <section class="library-ai-section" aria-label="可选处理方式">
              <div id="libraryAIAPIFields" class="library-ai-api-fields" hidden>
                <p>推荐 DeepSeek Pro，也可选择 MiniMax M3.1、豆包或其他兼容服务。模型名以服务商实际提供的 ID 为准，与读题服务分开保存。</p>
                <label for="libraryAIProvider">服务商</label><select id="libraryAIProvider"><option value="deepseek">DeepSeek（推荐）</option><option value="minimax">MiniMax M3.1（M Plan）</option><option value="doubao">豆包 API</option><option value="custom">其他兼容服务</option></select>
                <p id="libraryAIMinimaxHelp" hidden></p>
                <div id="libraryAIShareKey" class="library-ai-share" hidden>
                  <p id="libraryAIShareHelp"></p>
                  <div class="library-ai-share-actions">
                    <button id="libraryAIShareReadingKey" class="button" type="button">共用读题的 MiniMax 密钥</button>
                    <button id="libraryAIKeepOwnKey" class="button quiet" type="button" hidden>改用独立密钥</button>
                  </div>
                </div>
                <label for="libraryAIBaseURL">API 地址</label><input id="libraryAIBaseURL" type="text" placeholder="https://api.deepseek.com" autocomplete="off" spellcheck="false">
                <label for="libraryAIModel">模型 ID</label><input id="libraryAIModel" type="text" placeholder="服务商提供的模型 ID；豆包填写 Endpoint ID" autocomplete="off" spellcheck="false">
                <label class="library-ai-switch"><input id="libraryAIImages" type="checkbox"><span>此模型支持图片<small>仅在服务商确认支持时开启；纯文本模型不会跳过配图处理含图题。</small></span></label>
                <label class="library-ai-switch"><input id="libraryAIThinking" type="checkbox"><span>开启数学思考</span></label>
                <label for="libraryAIKey">API Key / MiniMax 订阅 Key</label>
                <div class="library-ai-key"><input id="libraryAIKey" type="password" autocomplete="off" autocapitalize="off" autocorrect="off" data-lpignore="true" data-1p-ignore="true" placeholder="留空保留当前服务商的密钥" spellcheck="false">
                  <button id="libraryAIKeyReveal" class="button" type="button" aria-label="查看密钥" title="查看密钥" aria-pressed="false"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg></button>
                </div><p id="libraryAIKeyHelp">默认隐藏。点击眼睛可临时查看，收起后恢复隐藏。</p>
                <label class="library-ai-switch"><input id="libraryAIClearKey" type="checkbox"><span>清除已保存的 API Key</span></label>
                <p>密钥用当前 Windows 用户加密保存。保存不联网；更换服务商后请使用对应的密钥。</p>
                <div class="library-ai-stored-keys" aria-label="各服务商已保存的密钥">
                  <strong>已保存的密钥</strong><p>星号仅表示已保存，不代表密钥长度。查看其他服务商不会切换当前 API 设置。</p>
                  ${Object.entries(providerNames).map(([provider, name]) => `<div class="library-ai-stored-key"><strong>${name}</strong><span id="libraryAIStoredMask-${provider}">未保存</span><button id="libraryAIStoredReveal-${provider}" class="button" type="button" aria-label="查看${name}第 1 条已保存的密钥" title="查看${name}第 1 条已保存的密钥" aria-pressed="false" hidden>${eyeIcon}</button><input id="libraryAIStoredValue-${provider}" type="password" readonly hidden autocomplete="off" data-lpignore="true" data-1p-ignore="true" aria-label="${name}第 1 条已保存的密钥"></div>`).join("")}
                </div>
                <strong>先确认能力，再生成</strong><p>保存后，用软件自带的合成题测试连接与响应；开启图像时还会测试合成图，不上传你的试卷。测试不评定数学水平；生成准确性仍需核对，测试与生成可能产生费用或消耗订阅额度。</p>
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
      if (id === "libraryAIMode") continue;
      const changed = () => {
        if (id === "libraryAIKey" && (revealing || revealedProvider)) hideKey();
        if (id === "libraryAIClearKey") {
          hideKey();
          $("libraryAIKey").disabled = $(id).checked;
          if ($(id).checked) { $("libraryAIKey").value = ""; state.needsKeyReplacement = false; }
        }
        if (id === "libraryAIKey" && $(id).value.trim()) state.needsKeyReplacement = false;
        if (["libraryAITags", "libraryAIAnswer"].includes(id)) renderTiming();
        const switchingProvider = id === "libraryAIProvider" && $(id).value !== state.provider;
        if (switchingProvider) {
          state.provider = $(id).value;
          const preset = state.current?.provider === state.provider ? state.current : defaults[state.provider];
          if (preset) {
            $("libraryAIBaseURL").value = preset.base_url;
            $("libraryAIModel").value = preset.model;
            $("libraryAIImages").checked = preset.supports_images;
            $("libraryAIThinking").checked = preset.thinking !== false;
          }
          clearSecret();
        }
        updateButtons();
        $("libraryAIResult").textContent = !state.dirty ? "" : switchingProvider ? "服务商已切换，请使用对应的密钥，再保存。" :
          state.needsKeyReplacement ? "新密钥未保存，请重新填写 API Key 后再保存。" :
            isAPI() ? "有未保存的设置；保存后再测试。" : "有未保存的设置。";
      };
      $(id).addEventListener("input", changed);
      if (id === "libraryAIProvider") $(id).addEventListener("change", changed);
    }
    $("libraryAITestConsent").addEventListener("change", updateButtons);
    $("libraryAIKeyReveal").addEventListener("click", () => { void toggleKey(); });
    $("libraryAIShareReadingKey").addEventListener("click", () => { void shareReadingKey(); });
    $("libraryAIKeepOwnKey").addEventListener("click", () => { void keepOwnKey(); });
    for (const provider of Object.keys(providerNames)) $(`libraryAIStoredReveal-${provider}`).addEventListener("click", () => { void toggleStoredKey(provider); });
    $("libraryAITest").addEventListener("click", () => { void test(); });
    $("libraryAICancel").addEventListener("click", close);
    $("libraryAIClose").addEventListener("click", close);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
    dialog.addEventListener("close", () => { state.session++; clearSecret(); state.dirty = false; state.busy = false; });
  }

  function clearSecret() {
    hideKey();
    state.needsKeyReplacement = false;
    $("libraryAIKey").value = "";
    $("libraryAIClearKey").checked = false;
    $("libraryAIKey").disabled = false;
    $("libraryAITestConsent").checked = false;
  }

  function renderMode() {
    $("libraryAIAPIFields").hidden = !isAPI();
    $("libraryAIAssistantHelp").hidden = true;
    $("libraryAIAdvanced").hidden = !isAPI();
    if (isAPI()) $("libraryAIAdvanced").open = true;
    $("libraryAISave").textContent = "保存 API 设置";
  }

  function hideKey() {
    ++revealEpoch;
    revealController?.abort(); revealController = null;
    if (revealTimer !== null) clearTimeout(revealTimer);
    revealTimer = null; revealing = false;
    const input = $("libraryAIKey");
    if (!input) return;
    revealedProvider = null; input.readOnly = false; input.type = "password";
    for (const provider of Object.keys(providerNames)) {
      const stored = $(`libraryAIStoredValue-${provider}`), eye = $(`libraryAIStoredReveal-${provider}`);
      if (stored) { stored.value = ""; stored.type = "password"; stored.hidden = true; }
      if (eye) { eye.setAttribute("aria-pressed", "false"); eye.setAttribute("aria-label", `查看${providerNames[provider]}第 1 条已保存的密钥`); eye.title = `查看${providerNames[provider]}第 1 条已保存的密钥`; }
    }
    const button = $("libraryAIKeyReveal");
    if (button) { button.setAttribute("aria-pressed", "false"); button.setAttribute("aria-label", "查看密钥"); button.title = "查看密钥"; }
    if ($("libraryAIKeyHelp")) $("libraryAIKeyHelp").textContent = "默认隐藏。点击眼睛可临时查看，收起后恢复隐藏。";
  }

  function showKey() {
    $("libraryAIKey").readOnly = false;
    $("libraryAIKey").type = "text";
    $("libraryAIKeyReveal").setAttribute("aria-pressed", "true");
    $("libraryAIKeyReveal").setAttribute("aria-label", "隐藏密钥");
    $("libraryAIKeyReveal").title = "隐藏密钥";
    $("libraryAIKeyHelp").textContent = "正在查看新填写的密钥，保存后会清空显示。";
    revealTimer = setTimeout(() => { hideKey(); updateButtons(); }, 60000);
  }

  async function toggleKey() {
    if (state.busy || !state.current || $("libraryAIClearKey").checked) return;
    if (revealing || revealedProvider || $("libraryAIKey").type === "text") { hideKey(); updateButtons(); return; }
    if ($("libraryAIKey").value) { showKey(); return; }
    await toggleStoredKey($("libraryAIProvider").value);
  }

  function storedKeyCount(provider) {
    const metadata = state.current?.keys?.[provider];
    if (metadata) return metadata.configured === true && metadata.count === 1 ? 1 : 0;
    return provider === state.current?.provider && state.current.key_configured === true ? 1 : 0;
  }

  function renderSavedKeys() {
    for (const provider of Object.keys(providerNames)) {
      const count = storedKeyCount(provider);
      $(`libraryAIStoredMask-${provider}`).textContent = count ? "•••••••• · 已保存 1 条" : "未保存";
      $(`libraryAIStoredReveal-${provider}`).hidden = !count;
    }
    renderSharing();
  }

  // 同一家 MiniMax 在读题和答案里各存一份密钥，就要粘贴两次。这里把
  // “填一次”的那条路摆出来，并且只在这条路真的可用时才出现：读题侧还没存
  // 密钥时按钮是灰的并说清去哪填，而不是给一个点了没反应的动作。
  function renderSharing() {
    const block = $("libraryAIShareKey");
    if (!block) return;
    const provider = $("libraryAIProvider").value;
    const shareable = (state.current?.shareable_from_reading || []).includes(provider);
    block.hidden = !isAPI() || !shareable;
    if (block.hidden) return;
    const own = (state.current?.keys || {})[provider] || {};
    const shared = own.shared_with_reading === true;
    // Whether the reading side holds a key comes from the reading side's own
    // status, handed in by the page: this module must never read that store.
    const readingReady = Boolean(state.reading?.[provider]?.configured);
    const share = $("libraryAIShareReadingKey"), keep = $("libraryAIKeepOwnKey");
    share.hidden = shared || !readingReady;
    share.disabled = state.busy || !state.current;
    share.textContent = own.configured ? "改用读题的 MiniMax 密钥" : "共用读题的 MiniMax 密钥";
    keep.hidden = !shared;
    keep.disabled = state.busy || !state.current;
    $("libraryAIShareHelp").textContent = shared
      ? "这里用的是读题那份 MiniMax 密钥（读题侧第 1 个账号）。以后改了读题的密钥，这里不会自动跟着变，要跟着改请再点一次共用。"
      : readingReady
        ? (own.configured ? "这里已经有一份独立的 MiniMax 密钥。共用会用读题的那份替换它，随时可以改回来。"
          : "读题这边已经保存了 MiniMax 密钥，可以直接共用，不用再粘贴一次。")
        : "读题那边还没有保存 MiniMax 密钥。先到“读题与切题”里填一次并保存，回来这里就能共用。";
  }

  async function shareReadingKey() {
    if (state.busy || !state.current) return;
    const session = ++state.session;
    state.busy = true; state.operation = "share";
    $("libraryAIResult").textContent = "正在从读题那边取密钥…";
    updateButtons();
    try {
      const body = await request(`${API}/share-reading-key`, { provider: "minimax" });
      if (session !== state.session || !isActive()) return;
      $("libraryAIKey").value = ""; $("libraryAIClearKey").checked = false;
      render(body);
      $("libraryAIResult").textContent = "已共用读题的 MiniMax 密钥。点“测试连接”验证一次即可。";
    } catch (error) {
      if (session === state.session && isActive()) $("libraryAIResult").textContent = error.message;
    } finally {
      if (session === state.session) { state.busy = false; state.operation = null; updateButtons(); }
    }
  }

  async function keepOwnKey() {
    if (state.busy || !state.current) return;
    try {
      const body = await request(API, { key: { action: "clear" } });
      $("libraryAIKey").value = ""; $("libraryAIClearKey").checked = false;
      render(body);
      $("libraryAIResult").textContent = "已清除共用的密钥，可以在下面粘贴自己的 Key 并保存。";
    } catch (error) {
      $("libraryAIResult").textContent = error.message;
    }
  }

  async function toggleStoredKey(provider) {
    if (state.busy || !state.current || !storedKeyCount(provider) || (provider === $("libraryAIProvider").value && $("libraryAIClearKey").checked)) return;
    if (revealing || revealedProvider === provider) { hideKey(); updateButtons(); return; }
    hideKey();
    const epoch = ++revealEpoch, session = state.session;
    const controller = new AbortController(); revealController = controller; revealing = true;
    const timeout = setTimeout(() => controller.abort(), 15000);
    $("libraryAIKeyHelp").textContent = "正在读取密钥…再点眼睛可取消。";
    try {
      const response = await fetch(`${API}/key/reveal`, { method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" },
        body: JSON.stringify({ provider, index: 0 }), cache: "no-store", signal: controller.signal });
      const body = await response.json();
      if (epoch !== revealEpoch || session !== state.session || !isActive() || controller.signal.aborted) return;
      if (!response.ok || body.provider !== provider || (body.index !== undefined && body.index !== 0) || typeof body.key !== "string" || !body.key) throw new Error("无法查看密钥，请重新读取设置后重试。");
      const stored = $(`libraryAIStoredValue-${provider}`); stored.value = body.key; stored.type = "text"; stored.hidden = false;
      const eye = $(`libraryAIStoredReveal-${provider}`); eye.setAttribute("aria-pressed", "true"); eye.setAttribute("aria-label", `隐藏${providerNames[provider]}第 1 条已保存的密钥`); eye.title = `隐藏${providerNames[provider]}第 1 条已保存的密钥`;
      revealedProvider = provider;
      $("libraryAIKeyHelp").textContent = `正在查看${providerNames[provider]}已保存的密钥；查看不会修改当前服务商或待保存的新密钥。`;
      revealTimer = setTimeout(() => { hideKey(); updateButtons(); }, 60000);
    } catch (error) {
      if (epoch === revealEpoch && session === state.session && isActive()) $("libraryAIKeyHelp").textContent = error.name === "AbortError" ? "读取已取消，可再次点击眼睛查看。" : "无法查看密钥，请重新读取设置后重试。";
    } finally {
      clearTimeout(timeout);
      if (epoch === revealEpoch) { revealController = null; revealing = false; updateButtons(); }
    }
  }

  function renderTiming() {
    $("libraryAITagsTiming").hidden = !$("libraryAITags").checked;
    $("libraryAIAnswerTiming").hidden = !$("libraryAIAnswer").checked;
  }

  function renderCapabilities() {
    const minimax = $("libraryAIProvider").value === "minimax";
    const model = $("libraryAIModel").value.trim();
    const latest = minimax && model === defaults.minimax.model;
    const textOnly = minimax && /^MiniMax-M2(?:\.(?:1|5|7)(?:-highspeed)?)?$/.test(model);
    const forcedThinking = latest || textOnly;
    $("libraryAIMinimaxHelp").hidden = !minimax;
    $("libraryAIMinimaxHelp").textContent = latest
      ? "M3.1 支持文字与图片，始终开启思考。当前需要 M Plan 订阅 Key，与按量 API Key 不通用；请在这里单独保存并显式测试。"
      : textOnly ? "M2 系列仅支持文字，始终开启思考；含图题请选择 M3.1、M3 或其他图文模型。"
        : "MiniMax M3 支持图文，思考可以关闭；其他型号的能力以官方文档和显式测试为准。读题密钥不会自动用于标签或答案。";
    if (textOnly) $("libraryAIImages").checked = false;
    if (forcedThinking) $("libraryAIThinking").checked = true;
    $("libraryAIImages").disabled = state.busy || !state.current || textOnly;
    $("libraryAIThinking").disabled = state.busy || !state.current || forcedThinking;
  }

  function effectiveSettings() {
    return JSON.stringify({
      features: { knowledge_tags: $("libraryAITags").checked, ai_answer: $("libraryAIAnswer").checked },
      on_intake: { tags: $("libraryAITagsIntake").checked, answer: $("libraryAIAnswerIntake").checked },
      provider: $("libraryAIProvider").value, base_url: $("libraryAIBaseURL").value.trim(), model: $("libraryAIModel").value.trim(),
      supports_images: $("libraryAIImages").checked, thinking: $("libraryAIThinking").checked,
      key: $("libraryAIClearKey").checked ? { action: "clear" } : $("libraryAIKey").value.trim() ? { action: "replace", value: $("libraryAIKey").value.trim() } : { action: "keep" }
    });
  }

  function syncDirty() {
    state.dirty = Boolean(isActive() && state.current && (state.needsKeyReplacement || (state.baseline !== null && effectiveSettings() !== state.baseline)));
    return state.dirty;
  }

  function updateButtons() {
    renderCapabilities(); syncDirty();
    $("libraryAISave").disabled = state.busy || !state.current || (isAPI() && state.needsKeyReplacement);
    $("libraryAITest").disabled = state.busy || state.dirty || !isAPI() || state.current?.mode !== "api" || !state.current?.configured || !$("libraryAITestConsent").checked;
    for (const id of fields) $(id).disabled = state.busy || !state.current;
    $("libraryAITagsIntake").disabled = state.busy || !state.current || !$("libraryAITags").checked;
    $("libraryAIAnswerIntake").disabled = state.busy || !state.current || !$("libraryAIAnswer").checked;
    $("libraryAITestConsent").disabled = state.busy || !state.current || !isAPI();
    $("libraryAIKey").disabled = state.busy || !state.current || $("libraryAIClearKey").checked;
    $("libraryAIKeyReveal").disabled = state.busy || !state.current || $("libraryAIClearKey").checked ||
      (!revealing && !revealedProvider && !$("libraryAIKey").value && !storedKeyCount($("libraryAIProvider").value));
    for (const provider of Object.keys(providerNames)) $(`libraryAIStoredReveal-${provider}`).disabled = state.busy || !state.current || !storedKeyCount(provider) || (provider === $("libraryAIProvider").value && $("libraryAIClearKey").checked);
    renderCapabilities(); renderSharing();
    if (state.inline) {
      $("libraryAICancel").disabled = state.busy;
      $("libraryAICancel").textContent = state.current ? "撤销更改" : "重新读取";
    }
  }

  async function request(url, payload) {
    const controller = new AbortController();
    requests.set(controller, { mutating: payload !== undefined });
    const timeout = setTimeout(() => controller.abort(), url.endsWith("/test") ? 420000 : 15000);
    try {
    const response = await fetch(url, { cache: "no-store", signal: controller.signal, ...(payload === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" }, body: JSON.stringify(payload)
    }) });
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
    } catch (error) {
      if (error.name === "AbortError") throw new Error(payload === undefined ? "读取超时，请重新读取设置。" : "操作结果尚未确认，请稍后重新读取设置；不会自动重试或再次测试。");
      throw error;
    } finally { clearTimeout(timeout); requests.delete(controller); }
  }

  function render(body) {
    state.current = body;
    state.baseline = null; clearSecret(); state.provider = body.provider;
    $("libraryAITags").checked = body.features.knowledge_tags;
    $("libraryAIAnswer").checked = body.features.ai_answer;
    $("libraryAITagsIntake").checked = body.on_intake.tags;
    $("libraryAIAnswerIntake").checked = body.on_intake.answer;
    renderTiming();
    $("libraryAIMode").value = "api";
    $("libraryAIProvider").value = body.provider;
    $("libraryAIBaseURL").value = body.base_url || "";
    $("libraryAIModel").value = body.model || "";
    $("libraryAIImages").checked = body.supports_images === true;
    $("libraryAIThinking").checked = body.thinking !== false;
    $("libraryAIAdvanced").open = body.mode === "api" || $("libraryAIAdvanced").open;
    renderMode();
    renderCapabilities(); renderSavedKeys();
    state.baseline = effectiveSettings(); state.dirty = false;
    $("libraryAIState").textContent = body.api_ready ? "答题 API 已通过测试，生成结果仍需核对。" : "请为答题助手配置 API，保存并测试后再生成。";
    $("libraryAIState").classList.toggle("error", !body.api_ready);
  }

  async function open() {
    if (window.APISettings?.open) return window.APISettings.open("answers");
    window.location.href = "/settings#api";
  }

  async function mount(host, options = {}) {
    if (!host) return;
    create(host, options);
    return activate();
  }

  async function activate() {
    if (!dialog || !state.inline || state.active) return;
    state.active = true;
    await load();
  }

  function deactivate() {
    if (isMutating() || syncDirty()) return false;
    state.active = false; ++state.session;
    for (const [controller, request] of requests) if (!request.mutating) controller.abort();
    clearSecret(); state.current = null; state.baseline = null;
    state.dirty = false; state.busy = false; state.operation = null;
    if (dialog) updateButtons();
    return true;
  }

  function isMutating() { return state.busy && ["save", "test", "share"].includes(state.operation); }

  async function load() {
    const session = ++state.session;
    state.current = null; state.baseline = null; state.dirty = false; state.busy = true; state.operation = "load";
    clearSecret();
    $("libraryAIAdvanced").open = false;
    $("libraryAITagsTiming").hidden = true;
    $("libraryAIAnswerTiming").hidden = true;
    $("libraryAIAPIFields").hidden = true;
    $("libraryAIAssistantHelp").hidden = true;
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
      if (session === state.session && isActive()) { state.busy = false; state.operation = null; updateButtons(); }
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
    if (syncDirty() && !window.confirm("这些设置还没保存。放弃更改并关闭？")) return;
    dialog.close();
  }

  async function save() {
    if (state.busy || !state.current) return;
    if (isAPI() && state.needsKeyReplacement) {
      $("libraryAIResult").textContent = "新密钥未保存，请重新填写 API Key 后再保存。";
      return;
    }
    const session = state.session;
    const payload = { mode: "api",
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
    hideKey();
    state.busy = true; state.operation = "save"; updateButtons();
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
        state.busy = false; state.operation = null; updateButtons();
      }
    }
  }

  async function test() {
    if (state.busy || syncDirty() || !isAPI() || state.current?.mode !== "api" || !state.current?.configured || !$("libraryAITestConsent").checked) return;
    const session = state.session;
    state.busy = true; state.operation = "test"; updateButtons();
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
      if (session === state.session && isActive()) { $("libraryAITestConsent").checked = false; state.busy = false; state.operation = null; updateButtons(); }
    }
  }

  function setReadingKeys(services) {
    // Read-side status, handed in by the page that already loaded it. The
    // answers panel asks "is there a MiniMax key on the reading side?" without
    // ever opening that store from here.
    state.reading = (services && typeof services === "object") ? { ...services } : {};
    if (dialog) updateButtons();
  }

  window.LibraryAISettings = Object.freeze({ open, mount, activate, deactivate, hideSecrets: hideKey, discard, setReadingKeys,
    hasUnsavedChanges: syncDirty, isBusy: () => state.busy, isMutating });
  window.addEventListener("beforeunload", (event) => {
    hideKey();
    if (!state.inline || !isActive() || (!syncDirty() && !state.busy)) return;
    event.preventDefault();
    event.returnValue = "";
  });
  window.addEventListener("hashchange", () => { if (state.inline && window.location.hash !== "#ai") { hideKey(); updateButtons(); } });
  document.addEventListener("visibilitychange", () => { if (document.hidden) { hideKey(); if (dialog) updateButtons(); } });
  document.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-library-ai-settings]")) { event.preventDefault(); void open(); }
  });
})();
