(() => {
  "use strict";
  const API = "/api/settings/library-ai";
  const state = { current: null, baseline: null, provider: "", dirty: false, busy: false, operation: null, session: 0, inline: false, embedded: false, active: false, keyPending: new Set(), reading: {}, confirm: null };
  const defaults = {
    deepseek: { base_url: "https://api.deepseek.com", model: "deepseek-v4-pro", supports_images: false },
    doubao: { base_url: "https://ark.cn-beijing.volces.com/api/v3", model: "", supports_images: false },
    minimax: { base_url: "https://api.minimax.cn/v1", model: "MiniMax-M3.1-Flash-Preview", supports_images: true },
    custom: { base_url: "", model: "", supports_images: false }
  };
  const providerNames = { deepseek: "DeepSeek", minimax: "MiniMax", doubao: "豆包", custom: "其他兼容服务" };
  // 一家一块，和“读题与切题”那边一个形状：名字、用途、已保存几条、新密钥填哪、
  // 删除在哪，全在同一个块里说完，不另开一张“已保存的密钥”表。
  const providerHints = { deepseek: "推荐 · 纯文字", minimax: "图文 · 可直接共用读题的密钥", doubao: "火山方舟 Endpoint", custom: "自己填地址和模型" };
  const providerOrder = ["deepseek", "minimax", "doubao", "custom"];
  const KEY_MASK = "****************";
  const KEY_NOTE = "已保存的密钥逐条隐藏显示，点眼睛可查看 60 秒。下面只填写新密钥；留空就保留原来的配置。";
  const eyeIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
  const fields = ["libraryAITags", "libraryAIAnswer", "libraryAITagsIntake", "libraryAIAnswerIntake", "libraryAIMode", "libraryAIProvider", "libraryAIBaseURL",
    "libraryAIModel", "libraryAIImages", "libraryAIThinking"];
  let dialog;
  const requests = new Map();
  const savedRows = new Map();
  const revealTimers = new Map();
  let revealEpoch = 0, revealController = null, revealedProvider = null, revealing = false;
  const $ = (id) => document.getElementById(id);
  const keyInput = (provider) => $(`libraryAIKey-${provider}`);

  const isAPI = () => true;
  const isActive = () => state.inline ? state.active : Boolean(dialog?.open);
  const askConfirm = (options) => state.confirm
    ? state.confirm(options)
    : Promise.resolve(window.confirm(`${options.title}\n\n${options.text || ""}`));

  function keyBlocks() {
    return providerOrder.map((provider) => `
        <section class="credential-service library-ai-key-block" data-library-service="${provider}">
          <div class="credential-service-head">
            <strong>${providerNames[provider]} <small>${providerHints[provider]}</small></strong>
            <span id="libraryAIKeyState-${provider}" class="api-state missing">未保存</span>
          </div>
          <ul id="libraryAISaved-${provider}" class="credential-saved-list" aria-label="${providerNames[provider]}已保存的密钥"></ul>
          ${provider === "minimax" ? `
          <div id="libraryAIShareKey" class="library-ai-share" hidden>
            <p id="libraryAIShareHelp"></p>
            <div class="library-ai-share-actions">
              <button id="libraryAIShareReadingKey" class="button" type="button">共用读题的 MiniMax 密钥</button>
              <button id="libraryAIKeepOwnKey" class="button quiet" type="button" hidden>改用独立密钥</button>
            </div>
          </div>` : ""}
          <label for="libraryAIKey-${provider}">新的 Key（留空就不改）</label>
          <div class="library-ai-key-row">
            <input id="libraryAIKey-${provider}" type="password" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false"
                   data-lpignore="true" data-1p-ignore="true" placeholder="粘贴 ${providerNames[provider]} 的 API Key" aria-label="${providerNames[provider]}新填的密钥">
            <button id="libraryAIKeyReveal-${provider}" class="button quiet small credential-eye" type="button"
                    aria-label="查看${providerNames[provider]}新填的密钥" title="临时查看这次填写的密钥（60 秒后隐藏）" aria-pressed="false">${eyeIcon}</button>
          </div>
          <div class="credential-service-actions"><button id="libraryAIDelete-${provider}" class="button small credential-delete" type="button" aria-label="删除 ${providerNames[provider]} 密钥" hidden>删除密钥</button></div>
        </section>`).join("\n");
  }

  function create(host, { embedded = false, confirm = null } = {}) {
    if (dialog) return;
    state.confirm = typeof confirm === "function" ? confirm : null;
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
      .library-ai-api-fields{display:grid;gap:11px}.library-ai-api-fields[hidden],.library-ai-section[hidden]{display:none}
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
      .library-ai-key-block>.library-ai-switch{color:var(--ink);font-size:13px;font-weight:400}
      .library-ai-key-row{display:flex;gap:8px;align-items:stretch}
      .library-ai-key-row>input{flex:1;min-width:0}
      .library-ai-key-row>.button{flex:none;padding:9px 12px;min-height:38px}
      .library-ai-key-row>.button svg{width:20px;height:20px;display:block;fill:none;stroke:currentColor;stroke-width:1.7}
      .library-ai-saved-row .credential-saved-value{font-family:var(--mono);font-size:12.5px;letter-spacing:.5px}
      .library-ai-share{display:grid;gap:8px;padding:11px 12px;border:1px solid var(--line);border-radius:10px;background:var(--accent-soft)}
      .library-ai-share[hidden]{display:none}
      .library-ai-share p{margin:0}
      .library-ai-share-actions{display:flex;flex-wrap:wrap;gap:8px}
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
                <p class="credential-storage-note">推荐 DeepSeek Pro，也可选择 MiniMax M3.1、豆包或其他兼容服务。模型名以服务商实际提供的 ID 为准。</p>

                <section class="credential-service" data-library-service="active">
                  <div class="credential-service-head">
                    <strong>生成用的服务 <small>标签和答案都用这一家</small></strong>
                    <span id="libraryAIActiveState" class="api-state">正在读取…</span>
                  </div>
                  <label for="libraryAIProvider">服务商</label><select id="libraryAIProvider"><option value="deepseek">DeepSeek（推荐）</option><option value="minimax">MiniMax M3.1（M Plan）</option><option value="doubao">豆包 API</option><option value="custom">其他兼容服务</option></select>
                  <p id="libraryAIMinimaxHelp" hidden></p>
                  <label for="libraryAIBaseURL">API 地址</label><input id="libraryAIBaseURL" type="text" placeholder="https://api.deepseek.com" autocomplete="off" spellcheck="false">
                  <label for="libraryAIModel">模型 ID</label><input id="libraryAIModel" type="text" placeholder="服务商提供的模型 ID；豆包填写 Endpoint ID" autocomplete="off" spellcheck="false">
                  <label class="library-ai-switch"><input id="libraryAIImages" type="checkbox"><span>此模型支持图片<small>仅在服务商确认支持时开启；纯文本模型不会跳过配图处理含图题。</small></span></label>
                  <label class="library-ai-switch"><input id="libraryAIThinking" type="checkbox"><span>开启数学思考</span></label>
                </section>

                <p id="libraryAIKeyNote" class="credential-storage-note" role="status" aria-live="polite">${KEY_NOTE}</p>
${keyBlocks()}
                <p class="credential-storage-note">密钥只加密保存在这台电脑上。查看不会重新保存；关闭窗口后立即隐藏。</p>
                <details class="credential-more">
                  <summary>密钥存在哪里？换一个服务商会怎样？</summary>
                  <p>密钥用这台电脑当前 Windows 用户的加密能力保存，不写入题库、日志或项目文件。保存时只检查填写格式，不上传文件、不消耗识读额度。</p>
                  <p>每家服务各存 1 个 Key。上面换一家服务商，只是换用哪一家；已经填好的其他家密钥原样留着，切回来还能用。删除某一家也只删那一家。</p>
                  <p>同一家 MiniMax 可以直接共用读题那份，不用再粘贴一次；共用以后改了读题的密钥，这里要再点一次才会跟着变。</p>
                </details>

                <section class="credential-service" data-library-service="test">
                  <div class="credential-service-head">
                    <strong>先确认能力，再生成</strong>
                    <span class="api-state">保存后先测一次</span>
                  </div>
                  <p>用软件自带的合成题测试连接与响应；开启图像时还会测试合成图，不上传你的试卷。测试不评定数学水平；生成准确性仍需核对，测试与生成可能产生费用或消耗订阅额度。</p>
                  <label class="library-ai-switch"><input id="libraryAITestConsent" type="checkbox"><span>我确认发起一次可能计费的 API 测试</span></label>
                  <div class="credential-service-actions"><button id="libraryAITest" class="button" type="button" disabled>测试连接</button></div>
                </section>
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
        if (["libraryAITags", "libraryAIAnswer"].includes(id)) renderTiming();
        const switchingProvider = id === "libraryAIProvider" && $(id).value !== state.provider;
        if (switchingProvider) {
          state.provider = $(id).value;
          cancelPendingReveal();
          const preset = state.current?.provider === state.provider ? state.current : defaults[state.provider];
          if (preset) {
            $("libraryAIBaseURL").value = preset.base_url;
            $("libraryAIModel").value = preset.model;
            $("libraryAIImages").checked = preset.supports_images;
            $("libraryAIThinking").checked = preset.thinking !== false;
          }
        }
        updateButtons();
        $("libraryAIResult").textContent = !state.dirty ? "" : switchingProvider ? "服务商已切换：这一家还没填密钥就先用别家，或在下面填好再保存。"
          : "有未保存的设置；保存后再测试。";
      };
      $(id).addEventListener("input", changed);
      if (id === "libraryAIProvider") $(id).addEventListener("change", changed);
    }
    for (const provider of providerOrder) {
      const input = keyInput(provider);
      input.addEventListener("input", () => {
        if (state.keyPending.has(provider) && input.value.trim()) state.keyPending.delete(provider);
        if (input.type === "text") hideTyped(provider);
        cancelPendingReveal();
        updateButtons();
      });
      $(`libraryAIKeyReveal-${provider}`).addEventListener("click", () => toggleTyped(provider));
      $(`libraryAIDelete-${provider}`).addEventListener("click", () => { void deleteKey(provider); });
    }
    $("libraryAITestConsent").addEventListener("change", updateButtons);
    $("libraryAIShareReadingKey").addEventListener("click", () => { void shareReadingKey(); });
    $("libraryAIKeepOwnKey").addEventListener("click", () => { void keepOwnKey(); });
    $("libraryAITest").addEventListener("click", () => { void test(); });
    $("libraryAICancel").addEventListener("click", close);
    $("libraryAIClose").addEventListener("click", close);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
    dialog.addEventListener("close", () => { state.session++; clearSecret(); state.dirty = false; state.busy = false; });
  }

  // ------------------------------------------------------------ 新填的密钥

  // 丢掉这一轮还没保存的一切：每家的输入框、未确认的替换、同意测试的勾。
  function clearSecret() {
    hideKey();
    state.keyPending.clear();
    for (const provider of providerOrder) { const input = keyInput(provider); if (input) input.value = ""; }
    $("libraryAITestConsent").checked = false;
  }

  function hideTyped(provider) {
    const input = keyInput(provider), eye = $(`libraryAIKeyReveal-${provider}`);
    clearTimeout(revealTimers.get(`typed:${provider}`));
    revealTimers.delete(`typed:${provider}`);
    if (input) input.type = "password";
    if (eye) { eye.setAttribute("aria-pressed", "false"); eye.setAttribute("aria-label", `查看${providerNames[provider]}新填的密钥`); eye.title = "临时查看这次填写的密钥（60 秒后隐藏）"; }
  }

  function toggleTyped(provider) {
    const input = keyInput(provider);
    if (state.busy || !input) return;
    if (input.type === "text") { hideTyped(provider); return; }
    if (!input.value) { keyNote(`${providerNames[provider]}这一栏还没有填新密钥。要看已保存的那一条，请点上面已保存密钥右边的眼睛。`); return; }
    input.type = "text";
    const eye = $(`libraryAIKeyReveal-${provider}`);
    eye.setAttribute("aria-pressed", "true");
    eye.setAttribute("aria-label", `隐藏${providerNames[provider]}新填的密钥`);
    eye.title = "隐藏这次填写的密钥";
    keyNote("正在查看这次填写的密钥，保存后会清空显示。");
    revealTimers.set(`typed:${provider}`, setTimeout(() => { hideTyped(provider); updateButtons(); }, 60000));
  }

  // ------------------------------------------------------------ 已保存的密钥

  function hideSavedRow(key, row) {
    row.epoch += 1;
    row.controller?.abort();
    row.controller = null;
    clearTimeout(row.timer);
    clearTimeout(row.timeout);
    clearTimeout(revealTimers.get(key));
    revealTimers.delete(key);
    row.timer = row.timeout = null;
    row.pending = row.visible = false;
    row.value.textContent = KEY_MASK;
    row.value.classList.remove("revealed");
    row.button.setAttribute("aria-pressed", "false");
    row.button.setAttribute("aria-label", `查看${providerNames[row.provider]}第 ${row.index + 1} 条已保存的密钥`);
    row.button.title = "临时查看密钥（60 秒后隐藏）";
    // 屏幕上一条明文都没有了，那行说明就回到平时那句话，别停在一句已经说完的话上。
    const stillShown = [...savedRows.values()].some((item) => item.visible)
      || providerOrder.some((provider) => keyInput(provider)?.type === "text");
    if (!stillShown) keyNote(KEY_NOTE);
  }

  function hideKey() {
    ++revealEpoch;
    revealController?.abort(); revealController = null;
    for (const timer of revealTimers.values()) clearTimeout(timer);
    revealTimers.clear();
    revealedProvider = null; revealing = false;
    savedRows.forEach((row, key) => hideSavedRow(key, row));
    for (const provider of providerOrder) hideTyped(provider);
    keyNote(KEY_NOTE);
  }

  function keyNote(text) { if ($("libraryAIKeyNote")) $("libraryAIKeyNote").textContent = text; }

  // 正在取的那条密钥：用户已经在别处动手了，就不要再让迟到的回答把明文放到
  // 屏幕上。已经显示出来的那几条各自有 60 秒计时，不在这里动。
  function cancelPendingReveal() {
    if (!revealing && !revealController) return;
    ++revealEpoch;
    revealController?.abort(); revealController = null; revealing = false;
  }

  function storedKeyCount(provider) {
    const metadata = state.current?.keys?.[provider];
    if (metadata) return metadata.configured === true ? Math.min(1, Math.max(0, Math.floor(Number(metadata.count) || 0))) : 0;
    return provider === state.current?.provider && state.current.key_configured === true ? 1 : 0;
  }

  function renderSavedKeys() {
    savedRows.forEach((row, key) => { hideSavedRow(key, row); savedRows.delete(key); });
    for (const provider of providerOrder) {
      const list = $(`libraryAISaved-${provider}`);
      if (!list) continue;
      list.replaceChildren();
      const count = storedKeyCount(provider);
      if (!count) {
        const empty = document.createElement("li");
        empty.className = "credential-saved-empty";
        empty.textContent = "暂无已保存密钥";
        list.append(empty);
        continue;
      }
      for (let index = 0; index < count; index += 1) {
        const item = document.createElement("li");
        item.className = "credential-saved-row library-ai-saved-row";
        const number = document.createElement("span");
        number.className = "credential-saved-number";
        number.textContent = `${index + 1}`;
        const value = document.createElement("span");
        value.className = "credential-saved-value";
        value.textContent = KEY_MASK;
        const eye = document.createElement("button");
        eye.type = "button";
        eye.className = "button quiet small credential-eye";
        eye.innerHTML = eyeIcon;
        const key = `${provider}:${index}`;
        const row = { provider, index, value, button: eye, epoch: 0, pending: false, visible: false, controller: null, timer: null, timeout: null };
        hideSavedRow(key, row);
        savedRows.set(key, row);
        eye.addEventListener("click", () => { void toggleSavedKey(provider, index); });
        item.append(number, value, eye);
        list.append(item);
      }
    }
    renderSharing();
  }

  async function toggleSavedKey(provider, index) {
    const row = savedRows.get(`${provider}:${index}`);
    if (!row || state.busy || !storedKeyCount(provider)) return;
    if (revealing || revealedProvider === provider) { hideKey(); updateButtons(); return; }
    hideKey();
    const epoch = ++revealEpoch, session = state.session;
    const controller = new AbortController(); revealController = controller; revealing = true;
    const timeout = setTimeout(() => controller.abort(), 15000);
    keyNote("正在读取密钥…再点眼睛可取消。");
    try {
      const response = await fetch(`${API}/key/reveal`, { method: "POST", headers: { "Content-Type": "application/json", "X-QB-Request": "1" },
        body: JSON.stringify({ provider, index: 0 }), cache: "no-store", signal: controller.signal });
      const body = await response.json();
      if (epoch !== revealEpoch || session !== state.session || !isActive() || controller.signal.aborted) return;
      if (!response.ok || body.provider !== provider || (body.index !== undefined && body.index !== 0) || typeof body.key !== "string" || !body.key) throw new Error("无法查看密钥，请重新读取设置后重试。");
      row.value.textContent = body.key;
      row.value.classList.add("revealed");
      row.visible = true;
      row.button.setAttribute("aria-pressed", "true");
      row.button.setAttribute("aria-label", `隐藏${providerNames[provider]}第 ${index + 1} 条已保存的密钥`);
      row.button.title = "隐藏密钥";
      revealedProvider = provider;
      keyNote(`正在查看 ${providerNames[provider]} 已保存的密钥；查看不会修改当前服务商或待保存的新密钥。`);
      row.timer = setTimeout(() => { hideSavedRow(`${provider}:${index}`, row); updateButtons(); }, 60000);
    } catch (error) {
      if (epoch === revealEpoch && session === state.session && isActive()) keyNote(error.name === "AbortError" ? "读取已取消，可再次点击眼睛查看。" : "无法查看密钥，请重新读取设置后重试。");
    } finally {
      clearTimeout(timeout);
      if (epoch === revealEpoch) { revealController = null; revealing = false; updateButtons(); }
    }
  }

  // ------------------------------------------------------------ 删除某一家

  async function deleteKey(provider) {
    if (state.busy || !state.current || !storedKeyCount(provider)) return;
    const active = $("libraryAIProvider").value;
    const payload = provider === active ? { key: { action: "clear" } } : { keys: { [provider]: { action: "clear" } } };
    const extra = provider === active ? "正在用的就是这一家，删掉后标签、答案生成会暂停。" : "其他服务和正在用的那一家都不受影响。";
    const confirmed = await askConfirm({ title: `删除 ${providerNames[provider]} 的密钥？`,
      text: `只删除这里已保存的 ${providerNames[provider]} 密钥。${extra}`, ok: "删除密钥", cancel: "取消", danger: true, focusCancel: true });
    if (!confirmed || state.busy || !state.current) return;
    const session = ++state.session;
    state.busy = true; state.operation = "save";
    $("libraryAIResult").textContent = `正在删除 ${providerNames[provider]} 密钥…`;
    updateButtons();
    try {
      const body = await request(API, payload);
      if (session !== state.session || !isActive()) return;
      render(body);
      $("libraryAIResult").textContent = `已删除 ${providerNames[provider]} 密钥，其他服务保持原样。`;
    } catch (error) {
      if (session === state.session && isActive()) $("libraryAIResult").textContent = error.message;
    } finally {
      if (session === state.session) { state.busy = false; state.operation = null; updateButtons(); }
    }
  }

  // ------------------------------------------------------------ 共用读题的密钥

  // 同一家 MiniMax 在读题和答案里各存一份密钥，就要粘贴两次。这里把
  // “填一次”的那条路摆出来，并且只在这条路真的可用时才出现：读题侧还没存
  // 密钥时按钮是灰的并说清去哪填，而不是给一个点了没反应的动作。
  function renderSharing() {
    const block = $("libraryAIShareKey");
    if (!block) return;
    const provider = $("libraryAIProvider").value;
    const shareable = (state.current?.shareable_from_reading || []).includes(provider);
    block.hidden = !isAPI() || !shareable || provider !== "minimax";
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
      render(body);
      $("libraryAIResult").textContent = "已清除共用的密钥，可以在下面粘贴自己的 Key 并保存。";
    } catch (error) {
      $("libraryAIResult").textContent = error.message;
    }
  }

  // ---------------------------------------------------------------- 表单状态

  function renderMode() {
    $("libraryAIAPIFields").hidden = !isAPI();
    $("libraryAIAssistantHelp").hidden = true;
    $("libraryAIAdvanced").hidden = !isAPI();
    if (isAPI()) $("libraryAIAdvanced").open = true;
    $("libraryAISave").textContent = "保存 API 设置";
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
      ? "M3.1 支持文字与图片，始终开启思考。当前需要 M Plan 订阅 Key，与按量 API Key 不通用；请在下面 MiniMax 那一栏保存并显式测试。"
      : textOnly ? "M2 系列仅支持文字，始终开启思考；含图题请选择 M3.1、M3 或其他图文模型。"
        : "MiniMax M3 支持图文，思考可以关闭；其他型号的能力以官方文档和显式测试为准。";
    if (textOnly) $("libraryAIImages").checked = false;
    if (forcedThinking) $("libraryAIThinking").checked = true;
    $("libraryAIImages").disabled = state.busy || !state.current || textOnly;
    $("libraryAIThinking").disabled = state.busy || !state.current || forcedThinking;
  }

  // 一栏新填的密钥 = 一次 replace；留空 = 保留原来那份。正在用的那一家走
  // “key”，其他家走“keys”，所以多填一家不用先切服务商、再存一次。
  function keyOperation(provider) {
    if (state.keyPending.has(provider)) return { action: "keep" };
    const value = (keyInput(provider)?.value || "").trim();
    return value ? { action: "replace", value } : { action: "keep" };
  }

  function effectiveSettings() {
    const active = $("libraryAIProvider").value;
    const keys = {};
    for (const provider of providerOrder) if (provider !== active) keys[provider] = keyOperation(provider);
    return JSON.stringify({
      features: { knowledge_tags: $("libraryAITags").checked, ai_answer: $("libraryAIAnswer").checked },
      on_intake: { tags: $("libraryAITagsIntake").checked, answer: $("libraryAIAnswerIntake").checked },
      provider: active, base_url: $("libraryAIBaseURL").value.trim(), model: $("libraryAIModel").value.trim(),
      supports_images: $("libraryAIImages").checked, thinking: $("libraryAIThinking").checked,
      key: keyOperation(active), keys
    });
  }

  function syncDirty() {
    state.dirty = Boolean(isActive() && state.current && (state.keyPending.size > 0 || (state.baseline !== null && effectiveSettings() !== state.baseline)));
    return state.dirty;
  }

  function updateButtons() {
    renderCapabilities(); syncDirty();
    const idle = !state.busy && Boolean(state.current);
    $("libraryAISave").disabled = !idle || state.keyPending.size > 0;
    $("libraryAITest").disabled = state.busy || state.dirty || !isAPI() || state.current?.mode !== "api" || !state.current?.configured || !$("libraryAITestConsent").checked;
    for (const id of fields) $(id).disabled = !idle;
    $("libraryAITagsIntake").disabled = !idle || !$("libraryAITags").checked;
    $("libraryAIAnswerIntake").disabled = !idle || !$("libraryAIAnswer").checked;
    $("libraryAITestConsent").disabled = !idle;
    const active = $("libraryAIProvider").value;
    const activeReady = Boolean(state.current?.configured);
    const chip = $("libraryAIActiveState");
    if (chip) {
      const label = !state.current ? "正在读取…" : state.current.mode === "assistant" ? "交给当前助手" : state.current.api_ready ? "已测通" : activeReady ? "已保存，未测试" : "缺密钥";
      chip.textContent = label;
      chip.className = `api-state ${state.current?.api_ready ? "ready" : "missing"}`;
    }
    for (const provider of providerOrder) {
      const count = storedKeyCount(provider);
      const state$ = $(`libraryAIKeyState-${provider}`);
      if (state$) {
        state$.textContent = `${provider === active ? "当前使用 · " : ""}${count ? `已保存 ${count} 个` : "未保存"}`;
        state$.className = `api-state ${count ? "ready" : "missing"}`;
      }
      const input = keyInput(provider);
      if (input) input.disabled = !idle || state.keyPending.has(provider);
      const eye = $(`libraryAIKeyReveal-${provider}`);
      // 眼睛一直可点：这一栏空着的时候，点了会说明该看的是上面已保存那一条，
      // 而不是给一个按了没反应的按钮。
      if (eye) eye.disabled = !idle;
      const remove = $(`libraryAIDelete-${provider}`);
      if (remove) { remove.hidden = !count; remove.disabled = !idle || !count; }
    }
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
    if (state.keyPending.size) {
      $("libraryAIResult").textContent = "有新的密钥还没保存，请重新填写后再保存。";
      return;
    }
    const session = state.session;
    const active = $("libraryAIProvider").value;
    const payload = { mode: "api",
      features: { knowledge_tags: $("libraryAITags").checked, ai_answer: $("libraryAIAnswer").checked },
      on_intake: { tags: $("libraryAITagsIntake").checked, answer: $("libraryAIAnswerIntake").checked } };
    const keys = {};
    for (const provider of providerOrder) {
      if (provider === active) continue;
      const operation = keyOperation(provider);
      if (operation.action !== "keep") keys[provider] = operation;
    }
    const key = keyOperation(active);
    Object.assign(payload, { provider: active, base_url: $("libraryAIBaseURL").value.trim(),
      model: $("libraryAIModel").value.trim(), supports_images: $("libraryAIImages").checked,
      thinking: $("libraryAIThinking").checked, reasoning_effort: "high", key });
    if (Object.keys(keys).length) payload.keys = keys;
    // 换了凭据却没保存成功，就当它没存：清空那一栏并挡住保存，逼着重填一次。
    const replaced = [...(key.action === "replace" ? [active] : []), ...Object.keys(keys)];
    let failed = false;
    hideKey();
    state.busy = true; state.operation = "save"; updateButtons();
    $("libraryAIResult").textContent = "正在加密保存…";
    try {
      const body = await request(API, payload);
      document.dispatchEvent(new CustomEvent("library-ai-settings-saved", { detail: body }));
      if (session !== state.session || !isActive()) return;
      render(body);
      $("libraryAIResult").textContent = body.mode === "assistant" ? "已保存；请把题目交给当前助手处理并写回。" :
        body.ready ? "已保存，生成按这两个开关分别执行。" : "已保存；请显式测试后再生成。";
    } catch (error) {
      failed = true;
      if (session === state.session && isActive()) $("libraryAIResult").textContent = error.message +
        (replaced.length ? "。新密钥未保存，请重新填写后再保存。" : "");
    } finally {
      if (key) delete key.value;
      for (const operation of Object.values(keys)) delete operation.value;
      if (session === state.session && isActive()) {
        clearSecret();
        for (const provider of failed ? replaced : []) state.keyPending.add(provider);
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
