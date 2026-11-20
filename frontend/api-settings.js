// 统一 API 配置与密钥管理前端模块（全站唯一入口：凭据池 + 读题模型 + 解题与标签 AI）
// 设计原则：
// 1. 服务商目录、默认值、凭据计数、解题与读题配置全部由单一接口 GET/POST /api/settings/api-config 驱动；
// 2. 任何时候只在一个宿主容器内就地渲染，不在页面与弹窗之间搬运 DOM 节点，杜绝表单状态错乱；
// 3. 密钥查看采用统一的 60 秒自动隐藏组件，严格本机同源、切后台/关闭立即清零明文；
// 4. 所有改动统一收敛到一个「保存全部 API 配置」动作和一套未保存离开守卫。
(function () {
  "use strict";

  const ROLE_LABELS = {
    cutting: "切题",
    reading: "读题",
    answering: "解题",
  };

  const MINIMAX_PLANS = [
    ["auto", "自动适应（推荐）"],
    ["plus", "Token Plan · Plus"],
    ["max", "Token Plan · Max"],
    ["ultra", "Token Plan · Ultra"],
    ["payg", "按量付费（Pay-As-You-Go）"],
  ];

  const SECRET_REVEAL_TIMEOUT_MS = 60000;
  const REQUEST_TIMEOUT_MS = 15000;
  const CREDENTIAL_MASK = "****************";

  const state = {
    host: null,
    mode: "inline", // "inline" | "modal"
    options: {},
    serverData: null,
    draft: null,
    baselineSignature: "",
    loadEpoch: 0,
    dialogSession: 0,
    loading: false,
    saving: false,
    testingTarget: "", // "" | "reading:<svc>" | "answering"
    closing: false,
    closeTask: null,
    returnFocus: null,
    lastStatusMessage: "",
    lastStatusTone: "",
    // key -> { value, timerId, pending, error }，key 形如 `${service}:${index}`
    revealedKeys: new Map(),
    dom: null,
  };

  /**
   * 构造无 XSS 风险的安全 DOM 节点。
   * @param {string} tag 标签名
   * @param {string} [className] 类名
   * @param {string} [text] 纯文本内容
   * @returns {HTMLElement}
   */
  function createEl(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function showToast(message, tone = "") {
    if (typeof state.options.toast === "function") {
      state.options.toast(message, tone);
      return;
    }
    if (typeof window.showToast === "function") {
      window.showToast(message, tone);
    }
  }

  async function askConfirm(options) {
    if (typeof state.options.confirm === "function") {
      return Boolean(await state.options.confirm(options));
    }
    if (typeof window.confirm === "function") {
      return window.confirm(`${options.title || ""}\n\n${options.text || ""}`.trim());
    }
    return false;
  }

  /**
   * 封装带超时控制与防缓存的同源 JSON 请求。
   * @param {string} url 请求地址
   * @param {object} [options] 请求选项
   */
  async function requestJson(url, { method = "GET", body, timeoutMs = REQUEST_TIMEOUT_MS } = {}) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    const headers = { Accept: "application/json" };
    if (method !== "GET") {
      headers["Content-Type"] = "application/json";
      headers["X-QB-Request"] = "1";
    }
    try {
      const response = await fetch(url, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        cache: "no-store",
        signal: controller.signal,
      });
      const text = await response.text();
      let payload = null;
      try {
        payload = text ? JSON.parse(text) : {};
      } catch {
        payload = null;
      }
      if (!response.ok) {
        const message = (payload && payload.error) || `请求失败（HTTP ${response.status}）`;
        const err = new Error(message);
        err.status = response.status;
        throw err;
      }
      if (!payload || typeof payload !== "object") {
        throw new Error("服务返回了无效的配置数据");
      }
      return payload;
    } catch (error) {
      if (error && error.name === "AbortError") {
        throw new Error("请求超时，请检查本地服务后重试");
      }
      throw error;
    } finally {
      window.clearTimeout(timer);
    }
  }

  function parseAccountsInput(raw) {
    return String(raw || "")
      .split(/[;\n\r]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function cloneDraftFromServer(data) {
    const reading = data.reading || {};
    const answering = data.answering || {};
    const credentials = data.credentials || {};
    const services = data.catalog?.services || [];
    const credDraft = {};
    for (const spec of services) {
      const info = credentials[spec.key] || { configured: false, count: 0 };
      credDraft[spec.key] = {
        input: "",
        clear: false,
        configured: Boolean(info.configured),
        count: Number(info.count || 0),
      };
    }

    const answerProfiles = {};
    for (const name of data.catalog?.answer_order || []) {
      const existing = answering.provider_profiles?.[name];
      const spec = services.find((item) => item.key === name);
      const defaults = spec?.answer_defaults || {};
      answerProfiles[name] = {
        model: existing?.model ?? defaults.model ?? "",
        base_url: existing?.base_url ?? defaults.base_url ?? "",
        supports_images: Boolean(existing?.supports_images ?? defaults.supports_images ?? false),
        thinking: Boolean(existing?.thinking ?? defaults.thinking ?? true),
        reasoning_effort: existing?.reasoning_effort || "high",
      };
    }
    if (answering.provider && answerProfiles[answering.provider]) {
      answerProfiles[answering.provider] = {
        model: answering.model ?? answerProfiles[answering.provider].model,
        base_url: answering.base_url ?? answerProfiles[answering.provider].base_url,
        supports_images: Boolean(answering.supports_images),
        thinking: Boolean(answering.thinking),
        reasoning_effort: answering.reasoning_effort || "high",
      };
    }

    return {
      reading: {
        primary: reading.primary || "minimax_m3",
        models: { ...(reading.models || {}) },
        plans: { minimax: reading.plans?.minimax || "auto" },
        testConsent: false,
        testResult: "",
        testTone: "",
      },
      credentials: credDraft,
      answering: {
        mode: answering.mode === "api" ? "api" : "assistant",
        provider: answering.provider || "deepseek",
        profiles: answerProfiles,
        features: {
          ai_answer: Boolean(answering.features?.ai_answer),
          knowledge_tags: Boolean(answering.features?.knowledge_tags),
        },
        on_intake: {
          answer: Boolean(answering.on_intake?.answer),
        },
        testConsent: false,
        testResult: "",
        testTone: "",
      },
    };
  }

  /**
   * 计算影响后端状态的干净草稿签名，用于准确判定「是否有未保存修改」。
   * 注意：纯 UI 测试勾选框与临时提示文案不计入未保存变更，避免点测试框误触发离开拦截。
   */
  function draftDirtySignature(draft) {
    if (!draft) return "";
    const credChanges = {};
    for (const [key, item] of Object.entries(draft.credentials || {})) {
      if (item.clear) credChanges[key] = "clear";
      else if (String(item.input || "").trim()) credChanges[key] = item.input;
    }
    return JSON.stringify({
      reading: {
        primary: draft.reading.primary,
        models: draft.reading.models,
        plans: draft.reading.plans,
      },
      credentials: credChanges,
      answering: {
        mode: draft.answering.mode,
        provider: draft.answering.provider,
        profiles: draft.answering.profiles,
        features: draft.answering.features,
        on_intake: draft.answering.on_intake,
      },
    });
  }

  function hasUnsavedChanges() {
    if (!state.draft || !state.baselineSignature) return false;
    return draftDirtySignature(state.draft) !== state.baselineSignature;
  }

  function isMutating() {
    return Boolean(state.saving || state.testingTarget);
  }

  function hideSecretEntry(entryKey) {
    const item = state.revealedKeys.get(entryKey);
    if (!item) return;
    if (item.timerId) window.clearTimeout(item.timerId);
    state.revealedKeys.delete(entryKey);
    const [service, indexStr] = entryKey.split(":");
    const row = state.host?.querySelector?.(`[data-secret-row="${service}:${indexStr}"]`);
    if (row) {
      const code = row.querySelector(".credential-secret-text");
      const btn = row.querySelector(".credential-reveal-btn");
      if (code) code.textContent = CREDENTIAL_MASK;
      if (btn) btn.textContent = "查看";
    }
  }

  function hideSecrets() {
    for (const key of Array.from(state.revealedKeys.keys())) {
      hideSecretEntry(key);
    }
  }

  async function toggleSecretReveal(service, index) {
    const entryKey = `${service}:${index}`;
    if (state.revealedKeys.has(entryKey)) {
      hideSecretEntry(entryKey);
      return;
    }
    // 同一时间只明文展示一条，避免满屏明文
    hideSecrets();
    const row = state.host?.querySelector?.(`[data-secret-row="${service}:${index}"]`);
    const btn = row?.querySelector?.(".credential-reveal-btn");
    const code = row?.querySelector?.(".credential-secret-text");
    if (btn) btn.disabled = true;
    const session = state.dialogSession;
    try {
      const data = await requestJson("/api/settings/api-config/key/reveal", {
        method: "POST",
        body: { service, index },
      });
      if (session !== state.dialogSession) return;
      const timerId = window.setTimeout(() => hideSecretEntry(entryKey), SECRET_REVEAL_TIMEOUT_MS);
      state.revealedKeys.set(entryKey, { value: data.key || "", timerId });
      if (code) code.textContent = data.key || "";
      if (btn) btn.textContent = "隐藏";
    } catch (error) {
      showToast(error.message || "无法读取已保存的密钥", "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function serviceEffectiveState(serviceKey) {
    const cred = state.draft?.credentials?.[serviceKey];
    if (!cred) return { configured: false, count: 0, label: "未配置", pending: false };
    const newAccounts = parseAccountsInput(cred.input);
    if (newAccounts.length > 0) {
      return { configured: true, count: newAccounts.length, label: `待保存 · ${newAccounts.length} 个新账号`, pending: true };
    }
    if (cred.clear) {
      return { configured: false, count: 0, label: "待清除", pending: true };
    }
    if (cred.configured && cred.count > 0) {
      return { configured: true, count: cred.count, label: `已保存 · ${cred.count} 个账号`, pending: false };
    }
    return { configured: false, count: 0, label: "未配置", pending: false };
  }

  function updateAnsweringStepTrack(data) {
    const step = document.getElementById("apiStepTrack-answers");
    if (!step || !data?.answering) return;
    const ans = data.answering;
    const ready = Boolean(ans.ready);
    step.className = ready ? "done" : "todo";
    const text = step.querySelector(".api-step-state");
    if (!text) return;
    if (ans.mode === "assistant") text.textContent = "AI 助手";
    else if (ans.verified) text.textContent = `${ans.provider || "独立 API"} 已测通`;
    else if (ans.configured) text.textContent = "待测试连接";
    else text.textContent = "未配置";
  }

  function buildReadingSection(data, draft) {
    const card = createEl("section", "credential-service api-unified-section");
    card.id = "apiSettingsReadingPanel";
    const head = createEl("div", "credential-service-head");
    const title = createEl("strong");
    title.append(createEl("span", "credential-step-number", "1"), " 先挑选谁来看图读题");
    const readingState = data.reading?.readiness?.ready ? "就绪" : "未就绪";
    const badge = createEl("span", `api-state ${data.reading?.readiness?.ready ? "is-ready" : ""}`, readingState);
    head.append(title, badge);
    card.append(head);

    card.append(createEl("p", "credential-purpose", "推荐魔搭（每天有免费额度）；已有其他服务的密钥可直接切换。"));

    const fieldset = createEl("fieldset", "provider-picker");
    fieldset.id = "credentialVisionPicker";
    fieldset.append(createEl("legend", "field-legend", "看图读题服务"));
    const visionServices = (data.catalog?.services || []).filter((spec) => spec.roles?.includes("reading"));

    for (const spec of visionServices) {
      const label = createEl("label", "provider-option");
      const radio = createEl("input");
      radio.type = "radio";
      radio.name = "apiUnifiedVisionProvider";
      radio.value = spec.engine;
      radio.checked = draft.reading.primary === spec.engine;
      radio.disabled = isMutating();
      radio.addEventListener("change", () => {
        draft.reading.primary = spec.engine;
        draft.reading.testConsent = false;
        draft.reading.testResult = "";
        renderContent();
      });
      const span = createEl("span", "", spec.label);
      if (spec.recommended) span.append(createEl("small", "", "推荐"));
      const st = serviceEffectiveState(spec.key);
      span.append(createEl("em", "provider-option-state", st.label));
      label.append(radio, span);
      fieldset.append(label);
    }

    // AI 助手（免视觉密钥）
    const assistantLabel = createEl("label", "provider-option");
    const assistantRadio = createEl("input");
    assistantRadio.type = "radio";
    assistantRadio.name = "apiUnifiedVisionProvider";
    assistantRadio.value = "assistant";
    assistantRadio.checked = draft.reading.primary === "assistant";
    assistantRadio.disabled = isMutating();
    assistantRadio.addEventListener("change", () => {
      draft.reading.primary = "assistant";
      renderContent();
    });
    const assistantSpan = createEl("span", "", "暂不配置");
    assistantSpan.append(createEl("small", "", "原图审核或 AI 助手"), createEl("em", "provider-option-state", "不用密钥"));
    assistantLabel.append(assistantRadio, assistantSpan);
    fieldset.append(assistantLabel);
    card.append(fieldset);

    const activeSpec = visionServices.find((spec) => spec.engine === draft.reading.primary);
    if (activeSpec) {
      const modelRow = createEl("label", "model-id-field");
      modelRow.append(createEl("span", "", `${activeSpec.label} 读题模型名称`));
      const listId = `apiReadingModels-${activeSpec.key}`;
      const datalist = createEl("datalist");
      datalist.id = listId;
      for (const m of activeSpec.reading_suggested_models || []) {
        const opt = document.createElement("option");
        opt.value = m;
        datalist.append(opt);
      }
      const input = createEl("input");
      input.type = "text";
      input.setAttribute("list", listId);
      input.autocomplete = "off";
      input.spellcheck = false;
      input.value = draft.reading.models[activeSpec.key] ?? activeSpec.reading_default_model ?? "";
      input.placeholder = activeSpec.reading_default_model || "填写视觉模型 ID";
      input.disabled = isMutating();
      input.addEventListener("input", () => {
        draft.reading.models[activeSpec.key] = input.value.trim();
        syncDirtyIndicator();
      });
      modelRow.append(input, datalist);
      card.append(modelRow);

      if (activeSpec.key === "minimax") {
        const planLabel = createEl("label", "model-id-field");
        planLabel.append(createEl("span", "", "MiniMax 会员档位（决定并发上限）"));
        const select = createEl("select");
        select.disabled = isMutating();
        for (const [val, text] of MINIMAX_PLANS) {
          const opt = document.createElement("option");
          opt.value = val;
          opt.textContent = text;
          select.append(opt);
        }
        select.value = draft.reading.plans.minimax || "auto";
        select.addEventListener("change", () => {
          draft.reading.plans.minimax = select.value;
          syncDirtyIndicator();
        });
        planLabel.append(select);
        card.append(planLabel);
      }

      // 读题连接测试（明确确认才发送合成图）
      const probeBox = createEl("div", "credential-connection-test");
      const consentLabel = createEl("label", "credential-consent");
      const consentBox = createEl("input");
      consentBox.type = "checkbox";
      consentBox.checked = draft.reading.testConsent;
      consentBox.disabled = isMutating();
      consentBox.addEventListener("change", () => {
        draft.reading.testConsent = consentBox.checked;
        testBtn.disabled = isMutating() || !draft.reading.testConsent;
      });
      consentLabel.append(consentBox, createEl("span", "", `我同意测试 ${activeSpec.label} 看图读题连接（发送一张本地合成图，可能消耗极少额度）`));

      const actions = createEl("div", "credential-connection-actions");
      const testBtn = createEl("button", "button", state.testingTarget === `reading:${activeSpec.key}` ? "正在测试…" : `测试 ${activeSpec.label} 看图连接`);
      testBtn.type = "button";
      testBtn.disabled = isMutating() || !draft.reading.testConsent;
      testBtn.addEventListener("click", () => {
        void runConnectionTest("reading", activeSpec.key);
      });
      const resultSpan = createEl("span", `settings-save-result ${draft.reading.testTone || ""}`, draft.reading.testResult || "");
      actions.append(testBtn, resultSpan);
      probeBox.append(consentLabel, actions);
      card.append(probeBox);
    } else {
      card.append(createEl("p", "settings-footnote", "当前选择暂不调用云端看图模型：切题后直接原图审核或交由 AI 助手读取。"));
    }
    return card;
  }

  function buildCredentialsSection(data, draft) {
    const section = createEl("section", "credential-service api-unified-section");
    section.id = "apiSettingsCredentialPanel";
    const head = createEl("div", "credential-service-head");
    const title = createEl("strong");
    title.append(createEl("span", "credential-step-number", "2"), " 统一服务商密钥池");
    const totalCount = Object.values(draft.credentials).reduce((acc, item) => acc + (item.configured ? item.count : 0), 0);
    const totalEl = createEl("span", "credential-saved-total", `已配置 ${totalCount} 个账号`);
    totalEl.id = "credentialSavedTotal";
    head.append(title, totalEl);
    section.append(head);
    section.append(createEl("p", "credential-storage-note", "所有服务的密钥统一保存在本机 Windows 加密文件中；同一服务商全站共用，只需填写一次。查看明文 60 秒后自动隐藏。"));

    const list = createEl("div", "api-credential-grid");
    const services = data.catalog?.services || [];
    for (const spec of services) {
      const cred = draft.credentials[spec.key];
      const card = createEl("article", "credential-service api-service-card");
      card.dataset.service = spec.key;

      const cardHead = createEl("div", "credential-service-head");
      const nameWrap = createEl("div", "api-service-title");
      nameWrap.append(createEl("strong", "", spec.label));
      for (const role of spec.roles || []) {
        nameWrap.append(createEl("span", "api-role-pill", ROLE_LABELS[role] || role));
      }
      const st = serviceEffectiveState(spec.key);
      const stateBadge = createEl("span", `api-state ${st.configured ? "is-ready" : ""}`, st.label);
      cardHead.append(nameWrap, stateBadge);
      card.append(cardHead);

      if (spec.note) {
        card.append(createEl("p", "credential-purpose", spec.note));
      }

      // 已保存密钥行（按 count 渲染 0..N 行）
      if (cred.configured && cred.count > 0 && !cred.clear) {
        const ul = createEl("ul", "credential-saved-list");
        ul.setAttribute("aria-label", `已保存的 ${spec.label} 密钥`);
        for (let idx = 0; idx < cred.count; idx++) {
          const li = createEl("li", "credential-saved-row");
          li.dataset.secretRow = `${spec.key}:${idx}`;
          const entryKey = `${spec.key}:${idx}`;
          const revealed = state.revealedKeys.get(entryKey);
          const idxLabel = createEl("span", "credential-saved-index", cred.count > 1 ? `账号 #${idx + 1}` : "已存密钥");
          const code = createEl("code", "credential-secret-text", revealed ? revealed.value : CREDENTIAL_MASK);
          const toggleBtn = createEl("button", "button small credential-reveal-btn", revealed ? "隐藏" : "查看");
          toggleBtn.type = "button";
          toggleBtn.addEventListener("click", () => {
            void toggleSecretReveal(spec.key, idx);
          });
          li.append(idxLabel, code, toggleBtn);
          ul.append(li);
        }
        card.append(ul);
      }

      // 输入新密钥
      const inputId = `apiKeyInput-${spec.key}`;
      const labelRow = createEl("label");
      labelRow.htmlFor = inputId;
      labelRow.append(createEl("span", "", spec.input_label || "新的密钥（留空就不改）"));
      if (spec.signup) {
        const link = createEl("a", "api-signup-link", "获取密钥");
        link.href = spec.signup;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        labelRow.append(" · ", link);
      }
      const input = createEl("input");
      input.id = inputId;
      input.type = "password";
      input.autocomplete = "off";
      input.setAttribute("autocapitalize", "off");
      input.setAttribute("autocorrect", "off");
      input.spellcheck = false;
      input.dataset.lpignore = "true";
      input.dataset["1pIgnore"] = "true";
      input.placeholder = spec.placeholder || `粘贴 ${spec.label} 密钥（多个账号可用英文分号 ; 隔开）`;
      input.value = cred.input;
      input.disabled = isMutating();
      input.addEventListener("input", () => {
        cred.input = input.value;
        if (input.value.trim()) cred.clear = false;
        syncDirtyIndicator();
      });
      card.append(labelRow, input);

      if (cred.configured) {
        const actions = createEl("div", "credential-service-actions");
        const clearBtn = createEl("button", "button small credential-delete", cred.clear ? "撤销清除（保留原密钥）" : "清除已存密钥");
        clearBtn.type = "button";
        clearBtn.disabled = isMutating();
        clearBtn.addEventListener("click", () => {
          cred.clear = !cred.clear;
          if (cred.clear) cred.input = "";
          renderContent();
        });
        actions.append(clearBtn);
        card.append(actions);
      }

      list.append(card);
    }
    section.append(list);
    return section;
  }

  function buildAnsweringSection(data, draft) {
    const panel = createEl("section", "credential-service api-unified-section");
    panel.id = "credentialAnswerPanel";
    const ansData = data.answering || {};
    const ans = draft.answering;

    const head = createEl("div", "credential-service-head");
    const title = createEl("strong");
    title.append(createEl("span", "credential-step-number", "3"), " 正式题库解题与知识点标签");
    const statusText = ans.mode === "assistant" ? "AI 助手模式" : ansData.verified ? "独立 API · 已测通" : "独立 API · 待测试";
    head.append(title, createEl("span", `api-state ${ansData.ready ? "is-ready" : ""}`, statusText));
    panel.append(head);

    // 功能开关区
    const switchesBox = createEl("div", "api-answering-switches");
    const answerSwitch = createSwitchRow(
      "AI 参考答案",
      `启用后可在题库内生成参考答案${typeof ansData.backlog?.missing_answer === "number" ? `（当前缺答案 ${ansData.backlog.missing_answer} 题）` : ""}`,
      ans.features.ai_answer,
      (checked) => {
        ans.features.ai_answer = checked;
        if (!checked) ans.on_intake.answer = false;
        renderContent();
      }
    );
    const intakeSwitch = createSwitchRow(
      "新入库题目自动排入生成参考答案",
      "仅在开启 AI 参考答案时生效；按当前所选执行方式排队",
      ans.on_intake.answer,
      (checked) => {
        ans.on_intake.answer = checked;
        syncDirtyIndicator();
      },
      !ans.features.ai_answer
    );
    const kSummary = ansData.knowledge || {};
    const tagSwitch = createSwitchRow(
      "知识点标签建议",
      kSummary.total ? `按正式题库标签目录建议（共 ${kSummary.chapters || 0} 章 ${kSummary.total} 个考点）` : "从正式题库标签目录筛选建议考点",
      ans.features.knowledge_tags,
      (checked) => {
        ans.features.knowledge_tags = checked;
        syncDirtyIndicator();
      }
    );
    switchesBox.append(answerSwitch, intakeSwitch, tagSwitch);
    panel.append(switchesBox);

    // 执行模式切换：AI 助手 vs 独立 API
    const modeFieldset = createEl("fieldset", "provider-picker");
    modeFieldset.append(createEl("legend", "field-legend", "执行方式"));
    for (const [val, text, sub] of [
      ["assistant", "AI 助手", "不调用独立 API，交由当前助手领取并写回"],
      ["api", "独立 API", "直接调用下方选定服务商（需先通过连接测试）"],
    ]) {
      const lb = createEl("label", "provider-option");
      const radio = createEl("input");
      radio.type = "radio";
      radio.name = "apiUnifiedAnswerMode";
      radio.value = val;
      radio.checked = ans.mode === val;
      radio.disabled = isMutating();
      radio.addEventListener("change", () => {
        ans.mode = val;
        renderContent();
      });
      const sp = createEl("span", "", text);
      sp.append(createEl("small", "", sub));
      lb.append(radio, sp);
      modeFieldset.append(lb);
    }
    panel.append(modeFieldset);

    if (ans.mode === "api") {
      const answerServices = (data.catalog?.answer_order || []).map(
        (key) => (data.catalog?.services || []).find((spec) => spec.key === key)
      ).filter(Boolean);

      const providerRow = createEl("label", "model-id-field");
      providerRow.append(createEl("span", "", "解题与标签服务商"));
      const select = createEl("select");
      select.disabled = isMutating();
      for (const spec of answerServices) {
        const st = serviceEffectiveState(spec.key);
        const opt = document.createElement("option");
        opt.value = spec.key;
        opt.textContent = `${spec.label}${st.configured ? `（${st.label}）` : "（未配置密钥）"}`;
        select.append(opt);
      }
      select.value = ans.provider;
      select.addEventListener("change", () => {
        ans.provider = select.value;
        ans.testConsent = false;
        ans.testResult = "";
        renderContent();
      });
      providerRow.append(select);
      panel.append(providerRow);

      // 共用提示（第 2 步统一管理，不再需要重复复制密钥按钮）
      const activeKeyState = serviceEffectiveState(ans.provider);
      const activeSpec = answerServices.find((spec) => spec.key === ans.provider);
      const isSharedVision = activeSpec?.roles?.includes("reading");
      panel.append(
        createEl(
          "p",
          "settings-footnote",
          activeKeyState.configured
            ? `已关联「步骤 2」中的 ${activeSpec?.label || ans.provider} 密钥（${activeKeyState.label}${isSharedVision ? " · 与读题全站共用，无需再次输入" : ""}）。`
            : `尚未填写 ${activeSpec?.label || ans.provider} 密钥：请直接在上方「步骤 2 · 统一服务商密钥池」填写。`
        )
      );

      const profile = ans.profiles[ans.provider] || {
        model: "",
        base_url: "",
        supports_images: false,
        thinking: true,
      };

      const modelField = createEl("label", "model-id-field");
      modelField.append(createEl("span", "", ans.provider === "doubao" ? "火山方舟 Endpoint ID（以 ep- 开头）" : "模型 ID"));
      const modelInput = createEl("input");
      modelInput.type = "text";
      modelInput.autocomplete = "off";
      modelInput.spellcheck = false;
      modelInput.value = profile.model;
      modelInput.placeholder = ans.provider === "doubao" ? "例如 ep-20250101xxxxxx-yyyy" : "填写服务商控制台的模型 ID";
      modelInput.disabled = isMutating();
      modelInput.addEventListener("input", () => {
        profile.model = modelInput.value.trim();
        syncDirtyIndicator();
      });
      modelField.append(modelInput);
      panel.append(modelField);

      const baseField = createEl("label", "model-id-field");
      baseField.append(createEl("span", "", "API 服务地址（Base URL）"));
      const baseInput = createEl("input");
      baseInput.type = "text";
      baseInput.autocomplete = "off";
      baseInput.spellcheck = false;
      baseInput.value = profile.base_url;
      baseInput.placeholder = ans.provider === "custom" ? "例如 https://api.example.com/v1" : "";
      baseInput.readOnly = ans.provider !== "custom";
      baseInput.disabled = isMutating();
      baseInput.addEventListener("input", () => {
        profile.base_url = baseInput.value.trim();
        syncDirtyIndicator();
      });
      baseField.append(baseInput);
      panel.append(baseField);

      const capsBox = createEl("div", "api-answering-switches");
      capsBox.append(
        createSwitchRow("支持看图输入", "勾选后向该模型附带题目原图或配图", Boolean(profile.supports_images), (checked) => {
          profile.supports_images = checked;
          syncDirtyIndicator();
        }),
        createSwitchRow("启用深度思考（Thinking）", "关闭可加快响应；部分仅支持思考的模型需保持开启", Boolean(profile.thinking), (checked) => {
          profile.thinking = checked;
          syncDirtyIndicator();
        })
      );
      panel.append(capsBox);

      // 独立解题连接测试
      const probeBox = createEl("div", "credential-connection-test");
      const consentLabel = createEl("label", "credential-consent");
      const consentBox = createEl("input");
      consentBox.type = "checkbox";
      consentBox.checked = ans.testConsent;
      consentBox.disabled = isMutating();
      consentBox.addEventListener("change", () => {
        ans.testConsent = consentBox.checked;
        testBtn.disabled = isMutating() || !ans.testConsent;
      });
      consentLabel.append(consentBox, createEl("span", "", "我同意运行一次合成数学连接测试（已保存后可测，可能产生少量费用）"));
      const actions = createEl("div", "credential-connection-actions");
      const testBtn = createEl("button", "button", state.testingTarget === "answering" ? "正在测试…" : "测试解题连接");
      testBtn.type = "button";
      testBtn.disabled = isMutating() || !ans.testConsent;
      testBtn.addEventListener("click", () => {
        void runConnectionTest("answering", ans.provider);
      });
      const noteText = ans.testResult || (ansData.verified ? `已测通（模型：${ansData.resolved_model || ansData.model}）` : "");
      const resSpan = createEl("span", `settings-save-result ${ans.testTone || (ansData.verified ? "is-success" : "")}`, noteText);
      actions.append(testBtn, resSpan);
      probeBox.append(consentLabel, actions);
      panel.append(probeBox);
    } else {
      panel.append(createEl("p", "settings-footnote", ansData.message || "由当前 AI 助手完成解题与标签建议；无需配置 API 密钥。"));
    }

    return panel;
  }

  function createSwitchRow(title, hint, checked, onChange, disabled = false) {
    const label = createEl("label", "settings-switch");
    const textWrap = createEl("span");
    textWrap.append(createEl("strong", "", title));
    if (hint) textWrap.append(createEl("small", "", hint));
    const input = createEl("input");
    input.type = "checkbox";
    input.setAttribute("role", "switch");
    input.checked = Boolean(checked);
    input.disabled = disabled || isMutating();
    input.addEventListener("change", () => onChange(input.checked));
    label.append(textWrap, input);
    return label;
  }

  function syncDirtyIndicator() {
    if (!state.dom) return;
    const dirty = hasUnsavedChanges();
    const { saveBtn, dirtyTag } = state.dom;
    if (saveBtn) {
      saveBtn.disabled = isMutating();
      saveBtn.textContent = state.saving ? "正在保存…" : dirty ? "保存全部 API 配置 *" : "保存全部 API 配置";
    }
    if (dirtyTag) {
      dirtyTag.hidden = !dirty;
      dirtyTag.textContent = dirty ? "有未保存的改动" : "";
    }
  }

  function renderContent() {
    if (!state.host) return;
    const root = createEl("div", "credential-ui api-unified-root");

    if (state.loading && !state.draft) {
      root.append(createEl("p", "settings-footnote", "正在读取全部 API 与密钥状态…"));
      state.host.replaceChildren(root);
      return;
    }
    if (!state.serverData || !state.draft) {
      const errBox = createEl("div", "credential-service");
      errBox.append(createEl("p", "settings-save-result is-error", state.lastStatusMessage || "未能读取配置"));
      const retry = createEl("button", "button small", "重试加载");
      retry.type = "button";
      retry.addEventListener("click", () => {
        void loadConfig();
      });
      errBox.append(retry);
      root.append(errBox);
      state.host.replaceChildren(root);
      return;
    }

    const body = createEl("div", "credential-body");
    body.append(
      buildReadingSection(state.serverData, state.draft),
      buildCredentialsSection(state.serverData, state.draft),
      buildAnsweringSection(state.serverData, state.draft)
    );

    const footer = createEl("footer", "credential-actions");
    const statusMsg = createEl("span", `settings-save-result ${state.lastStatusTone || ""}`, state.lastStatusMessage || "");
    statusMsg.id = "credentialResult";
    statusMsg.setAttribute("role", "status");
    const dirtyTag = createEl("span", "api-dirty-badge");
    dirtyTag.hidden = true;
    const saveBtn = createEl("button", "button primary", "保存全部 API 配置");
    saveBtn.id = "credentialSave";
    saveBtn.type = "button";
    saveBtn.addEventListener("click", () => {
      void save();
    });

    footer.append(statusMsg, dirtyTag, saveBtn);
    if (state.mode === "modal") {
      const cancelBtn = createEl("button", "button", "关闭");
      cancelBtn.id = "credentialCancel";
      cancelBtn.type = "button";
      cancelBtn.disabled = isMutating();
      cancelBtn.addEventListener("click", () => {
        void requestModalClose();
      });
      footer.append(cancelBtn);
    }

    root.append(body, footer);
    state.host.replaceChildren(root);
    state.dom = { saveBtn, statusMsg, dirtyTag };
    syncDirtyIndicator();
  }

  async function loadConfig({ preserveDraftIfDirty = false } = {}) {
    const epoch = ++state.loadEpoch;
    state.loading = true;
    renderContent();
    try {
      const data = await requestJson("/api/settings/api-config");
      if (epoch !== state.loadEpoch) return null;
      state.serverData = data;
      updateAnsweringStepTrack(data);
      if (!preserveDraftIfDirty || !hasUnsavedChanges()) {
        state.draft = cloneDraftFromServer(data);
        state.baselineSignature = draftDirtySignature(state.draft);
      }
      state.lastStatusMessage = "";
      state.lastStatusTone = "";
      return data;
    } catch (error) {
      if (epoch !== state.loadEpoch) return null;
      state.lastStatusMessage = error.message || "读取配置失败";
      state.lastStatusTone = "is-error";
      return null;
    } finally {
      if (epoch === state.loadEpoch) {
        state.loading = false;
        renderContent();
      }
    }
  }

  function buildSavePayload() {
    const draft = state.draft;
    const credOps = {};
    for (const [service, item] of Object.entries(draft.credentials)) {
      const accounts = parseAccountsInput(item.input);
      if (accounts.length > 0) {
        credOps[service] = { action: "replace", accounts };
      } else if (item.clear) {
        credOps[service] = { action: "clear" };
      } else {
        credOps[service] = { action: "keep" };
      }
    }

    const ans = draft.answering;
    const activeProfile = ans.profiles[ans.provider] || {};
    const extraProfiles = {};
    for (const [name, profile] of Object.entries(ans.profiles)) {
      if (name !== ans.provider) extraProfiles[name] = { ...profile };
    }

    return {
      credentials: credOps,
      reading: {
        primary: draft.reading.primary,
        models: { ...draft.reading.models },
        plans: { ...draft.reading.plans },
      },
      answering: {
        mode: ans.mode,
        provider: ans.provider,
        model: activeProfile.model ?? "",
        base_url: activeProfile.base_url ?? "",
        supports_images: Boolean(activeProfile.supports_images),
        thinking: Boolean(activeProfile.thinking),
        reasoning_effort: activeProfile.reasoning_effort || "high",
        features: { ...ans.features },
        on_intake: { tags: false, answer: Boolean(ans.on_intake.answer) },
        provider_profiles: extraProfiles,
      },
    };
  }

  async function save() {
    if (!state.draft || isMutating()) return false;
    state.saving = true;
    state.lastStatusMessage = "正在加密保存配置…";
    state.lastStatusTone = "";
    renderContent();
    try {
      const payload = buildSavePayload();
      const updated = await requestJson("/api/settings/api-config", {
        method: "POST",
        body: payload,
      });
      hideSecrets();
      state.serverData = updated;
      state.draft = cloneDraftFromServer(updated);
      state.baselineSignature = draftDirtySignature(state.draft);
      state.lastStatusMessage = updated.message || "API 配置已保存";
      state.lastStatusTone = "is-success";
      updateAnsweringStepTrack(updated);
      showToast(state.lastStatusMessage, "success");
      if (typeof state.options.onStatusChange === "function") {
        void state.options.onStatusChange(updated);
      }
      document.dispatchEvent(new CustomEvent("api-config-saved", { detail: updated }));
      document.dispatchEvent(new CustomEvent("library-ai-settings-saved", { detail: updated.answering }));
      return true;
    } catch (error) {
      state.lastStatusMessage = error.message || "保存失败，请检查填写内容";
      state.lastStatusTone = "is-error";
      showToast(state.lastStatusMessage, "error");
      return false;
    } finally {
      state.saving = false;
      renderContent();
    }
  }

  async function runConnectionTest(target, service) {
    if (!state.draft || isMutating()) return;
    if (hasUnsavedChanges()) {
      const savedFirst = await askConfirm({
        title: "先保存当前修改再测试？",
        text: "你修改的配置尚未保存。测试连接使用已保存的配置和密钥；是否立即保存后继续测试？",
        ok: "保存并测试",
        cancel: "取消",
      });
      if (!savedFirst) return;
      const ok = await save();
      if (!ok) return;
    }
    state.testingTarget = target === "reading" ? `reading:${service}` : "answering";
    renderContent();
    try {
      const res = await requestJson("/api/settings/api-config/test", {
        method: "POST",
        body: target === "reading" ? { target: "reading", service, confirm: true } : { target: "answering", confirm: true },
      });
      if (res.state) {
        state.serverData = res.state;
        updateAnsweringStepTrack(res.state);
      }
      const msg = res.message || (res.ok ? "连接测试通过" : "连接测试未通过");
      if (target === "reading") {
        state.draft.reading.testResult = msg;
        state.draft.reading.testTone = res.ok ? "is-success" : "is-error";
      } else {
        state.draft.answering.testResult = msg;
        state.draft.answering.testTone = res.ok ? "is-success" : "is-error";
      }
      showToast(msg, res.ok ? "success" : "error");
    } catch (error) {
      const msg = error.message || "连接测试失败";
      if (target === "reading") {
        state.draft.reading.testResult = msg;
        state.draft.reading.testTone = "is-error";
      } else {
        state.draft.answering.testResult = msg;
        state.draft.answering.testTone = "is-error";
      }
      showToast(msg, "error");
    } finally {
      state.testingTarget = "";
      renderContent();
    }
  }

  async function discard() {
    if (isMutating()) return false;
    hideSecrets();
    if (state.serverData) {
      state.draft = cloneDraftFromServer(state.serverData);
      state.baselineSignature = draftDirtySignature(state.draft);
    }
    state.lastStatusMessage = "";
    state.lastStatusTone = "";
    renderContent();
    return true;
  }

  async function prepareLeave() {
    if (isMutating()) {
      showToast("正在保存或测试 API 配置，请稍候再离开。", "warn");
      return false;
    }
    if (!hasUnsavedChanges()) return true;
    const discardChanges = await askConfirm({
      title: "API 配置还没保存",
      text: "你有尚未保存的密钥、模型或开关改动；离开会放弃本次修改，已保存的配置保持原样。",
      ok: "放弃并离开",
      cancel: "留下继续设置",
      danger: true,
      focusCancel: true,
    });
    if (!discardChanges) return false;
    await discard();
    return true;
  }

  async function requestModalClose() {
    const dialog = document.getElementById("credentialDialog");
    if (!dialog || !dialog.open) return true;
    if (isMutating()) {
      showToast("正在保存或测试，请稍候。", "warn");
      return false;
    }
    if (!hasUnsavedChanges()) {
      hideSecrets();
      dialog.close();
      return true;
    }
    if (state.closing) return false;
    state.closing = true;
    try {
      const saveAndClose = await askConfirm({
        title: "API 配置有未保存改动",
        text: "要保存本次修改，还是放弃改动直接关闭窗口？",
        ok: "保存并关闭",
        cancel: "放弃改动",
      });
      if (saveAndClose) {
        const ok = await save();
        if (!ok) return false;
      } else {
        await discard();
      }
      hideSecrets();
      if (dialog.open) dialog.close();
      return true;
    } finally {
      state.closing = false;
    }
  }

  async function mount(hostElement, options = {}) {
    if (!hostElement) return null;
    state.host = hostElement;
    state.mode = options.mode === "modal" ? "modal" : "inline";
    state.options = { ...state.options, ...options };
    return loadConfig({ preserveDraftIfDirty: Boolean(options.preserveDraftIfDirty) });
  }

  async function open(options = {}) {
    const { section = "", ...rest } = options || {};
    state.options = { ...state.options, ...rest };
    const dialog = document.getElementById("credentialDialog");
    const modalBody = document.getElementById("credentialDialogBody");
    if (!dialog || !modalBody) return;
    if (window.location.pathname === "/settings") {
      // 如果已在设置页，直接滚动定位而不重复弹窗
      if (section === "answering") scrollToAnswering();
      return;
    }
    state.returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    ++state.dialogSession;
    hideSecrets();
    if (!dialog.open) dialog.showModal();
    await mount(modalBody, { ...state.options, mode: "modal" });
    if (section === "answering") scrollToAnswering();
  }

  function scrollToAnswering() {
    const target = document.getElementById("credentialAnswerPanel");
    if (target && typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ block: "start" });
    }
  }

  function wireDialogGuards() {
    const dialog = document.getElementById("credentialDialog");
    if (dialog && !dialog.dataset.unifiedWired) {
      dialog.dataset.unifiedWired = "1";
      dialog.addEventListener("cancel", (event) => {
        event.preventDefault();
        void requestModalClose();
      });
      dialog.querySelectorAll("[data-close]").forEach((btn) => {
        btn.addEventListener("click", () => {
          void requestModalClose();
        });
      });
      dialog.addEventListener("close", () => {
        ++state.dialogSession;
        hideSecrets();
        const back = state.returnFocus;
        state.returnFocus = null;
        if (back && typeof back.focus === "function") back.focus({ preventScroll: true });
      });
    }
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) hideSecrets();
    });
    window.addEventListener("pagehide", () => hideSecrets());
  }

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", wireDialogGuards, { once: true });
    } else {
      wireDialogGuards();
    }
  }

  window.APISettings = Object.freeze({
    mount,
    open,
    close: requestModalClose,
    refresh: loadConfig,
    save,
    discard,
    prepareLeave,
    hasUnsavedChanges,
    isMutating,
    hideSecrets,
    scrollToAnswering,
    // 仅供前端单元测试检查核心逻辑
    _test: {
      parseAccountsInput,
      draftDirtySignature,
      cloneDraftFromServer,
      state,
    },
  });
})();
