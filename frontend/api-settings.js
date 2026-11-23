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
  const TEST_REQUEST_TIMEOUT_MS = 100000;
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
    // 折叠面板展开态、输入框本地明文查看态与当前会话已测通的读题模型记录
    openDetails: new Map(),
    visibleInputs: new Set(),
    readingVerified: new Map(),
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

  /**
   * 在文本输入框内按 Enter 时直接触发保存（避开输入法选词与组合键）。
   * @param {HTMLInputElement} input 输入框节点
   */
  function bindEnterToSave(input) {
    input.addEventListener("keydown", (event) => {
      if (!event || event.isComposing || event.keyCode === 229) return;
      if (event.key !== "Enter" || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return;
      if (typeof event.preventDefault === "function") event.preventDefault();
      if (isMutating()) return;
      if (hasUnsavedChanges()) {
        void save();
      } else {
        showToast("当前配置已是最新，无需保存", "success");
      }
    });
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
        // 连接测试在探测失败时返回 HTTP 409 与 { ok: false, message, state }，
        // 若仅读取 payload.error 会把真实诊断信息吞掉，只剩「请求失败（HTTP 409）」。
        const message = (payload && (payload.error || payload.message)) || `请求失败（HTTP ${response.status}）`;
        const err = new Error(message);
        err.status = response.status;
        err.payload = payload;
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

    const bgFeature = Array.isArray(data.features)
      ? data.features.find((item) => item && item.key === "auto_tag_background")
      : null;

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
        mode: "api",
        provider: answering.provider || "deepseek",
        profiles: answerProfiles,
        features: {
          ai_answer: Boolean(answering.features?.ai_answer),
          knowledge_tags: Boolean(answering.features?.knowledge_tags),
        },
        autoTagBackground: Boolean(bgFeature?.enabled),
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
        autoTagBackground: Boolean(draft.answering.autoTagBackground),
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
    state.visibleInputs.clear();
    if (state.dom?.inputRows) {
      for (const row of state.dom.inputRows.values()) {
        if (row.input) row.input.type = "password";
        if (row.toggleBtn) {
          row.toggleBtn.textContent = "显示";
          row.toggleBtn.setAttribute("aria-pressed", "false");
        }
      }
    }
  }

  /**
   * 记忆 <details> 折叠面板的展开态，避免交互重绘时意外收起。
   * @param {HTMLDetailsElement} detailsEl 折叠节点
   * @param {string} key 状态键
   * @param {boolean} defaultOpen 初始默认是否展开
   */
  function wireDetailsPersistence(detailsEl, key, defaultOpen = false) {
    const stored = state.openDetails.get(key);
    detailsEl.open = stored !== undefined ? stored : Boolean(defaultOpen);
    detailsEl.addEventListener("toggle", () => {
      state.openDetails.set(key, Boolean(detailsEl.open));
    });
  }

  /**
   * 展开（如需）并滚动聚焦到指定服务商的密钥输入框。
   * @param {string} serviceKey 服务键名
   */
  function focusServiceKeyInput(serviceKey) {
    if (ANSWER_ONLY_KEY_SERVICES.has(serviceKey)) {
      state.openDetails.set("more-credentials", true);
      if (state.dom?.moreCredentialsDetails) {
        state.dom.moreCredentialsDetails.open = true;
      }
    }
    const input = document.getElementById(`apiKeyInput-${serviceKey}`);
    if (input) {
      if (typeof input.scrollIntoView === "function") {
        input.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      if (typeof input.focus === "function") {
        input.focus({ preventScroll: true });
      }
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

  function updateStepTracks(data) {
    if (!data) return;
    const mineruStep = document.getElementById("apiStepTrack-mineru");
    if (mineruStep && data.credentials?.mineru) {
      const mineruReady = Boolean(data.credentials.mineru.configured);
      mineruStep.className = mineruReady ? "done" : "todo";
      const mineruText = mineruStep.querySelector?.(".api-step-state");
      if (mineruText) mineruText.textContent = mineruReady ? "已配置" : "还没配";
    }

    const readerStep = document.getElementById("apiStepTrack-reader");
    if (readerStep && data.reading) {
      const visionSpecs = (data.catalog?.services || []).filter((spec) => spec.roles?.includes("reading"));
      const activeSpec = visionSpecs.find((spec) => spec.engine === data.reading.primary);
      const assistantMode = Boolean(data.reading.assistant_mode || data.reading.primary === "assistant");
      const ready = Boolean(data.reading?.readiness?.ready);
      readerStep.className = ready || assistantMode ? "done" : "todo";
      const readerText = readerStep.querySelector?.(".api-step-state");
      if (readerText) {
        if (assistantMode) {
          readerText.textContent = "AI 助手";
        } else if (activeSpec) {
          const verifiedModel = state.readingVerified.get(activeSpec.key);
          const savedModel = data.reading.models?.[activeSpec.key] ?? activeSpec.reading_default_model ?? "";
          if (verifiedModel !== undefined && verifiedModel === savedModel) {
            readerText.textContent = `${activeSpec.label} 已测通`;
          } else if (ready) {
            readerText.textContent = `${activeSpec.label}（已填密钥）`;
          } else {
            readerText.textContent = "还没配";
          }
        } else {
          readerText.textContent = ready ? (data.reading?.readiness?.label || "已配置") : "还没配";
        }
      }
    }

    const ansStep = document.getElementById("apiStepTrack-answers");
    if (ansStep && data.answering) {
      const ans = data.answering;
      const ready = Boolean(ans.ready);
      ansStep.className = ready ? "done" : "todo";
      const text = ansStep.querySelector?.(".api-step-state");
      if (text) {
        if (ans.mode === "assistant") text.textContent = "AI 助手";
        else if (ans.verified) text.textContent = `${ans.provider || "独立 API"} 已测通`;
        else if (ans.configured) text.textContent = "待测试连接";
        else text.textContent = "未配置";
      }
    }
  }

  function readingBadgeState(data, draft, activeSpec) {
    const serverReady = Boolean(data?.reading?.readiness?.ready);
    if (!activeSpec || draft.reading.primary === "assistant") {
      return { tone: "", text: "暂不调用云端" };
    }
    const st = serviceEffectiveState(activeSpec.key);
    const currentModel = draft.reading.models[activeSpec.key] ?? activeSpec.reading_default_model ?? "";
    const verifiedModel = state.readingVerified.get(activeSpec.key);
    if (st.pending) {
      return { tone: "is-pending", text: st.configured ? "待保存密钥" : "待清除密钥" };
    }
    if (verifiedModel !== undefined && verifiedModel === currentModel && st.configured) {
      return { tone: "is-ready", text: "已测通 ✓" };
    }
    if (serverReady || st.configured) {
      return { tone: "is-warn", text: "已填密钥（未测试）" };
    }
    return { tone: "", text: "未配密钥" };
  }

  function answeringBadgeState(data, draft) {
    const ansData = data?.answering || {};
    const ans = draft.answering;
    const st = serviceEffectiveState(ans.provider);
    if (st.pending) {
      return { tone: "is-pending", text: st.configured ? "待保存密钥" : "待清除密钥" };
    }
    if (ansData.verified && ans.provider === ansData.provider) {
      return { tone: "is-ready", text: "已测通 ✓" };
    }
    if (st.configured) {
      return { tone: "is-warn", text: "待测试连接" };
    }
    return { tone: "", text: "未配密钥" };
  }

  function buildMissingKeyBar(serviceKey, label) {
    const bar = createEl("div", "api-missing-key-bar");
    bar.append(createEl("span", "", `尚未填写 ${label} 密钥，填入后即可测试连接。`));
    const gotoBtn = createEl("button", "button small", "去填写密钥");
    gotoBtn.type = "button";
    gotoBtn.addEventListener("click", () => {
      focusServiceKeyInput(serviceKey);
    });
    bar.append(gotoBtn);
    return bar;
  }

  const ANSWER_SUGGESTED_MODELS = Object.freeze({
    minimax: ["MiniMax-M3.1-Flash-Preview", "MiniMax-M3"],
    deepseek: ["deepseek-chat", "deepseek-reasoner", "deepseek-v4-pro"],
    modelscope: ["Qwen/Qwen3.5-35B-A3B", "Qwen/Qwen3.5-122B-A10B", "Qwen/Qwen3.5-397B-A17B", "Qwen/Qwen3.5-27B"],
    siliconflow: ["Qwen/Qwen3-VL-32B-Instruct", "Qwen/Qwen3-VL-30B-A3B-Thinking", "Qwen/Qwen3-VL-30B-A3B-Instruct"],
  });

  const ANSWER_ONLY_KEY_SERVICES = new Set(["deepseek", "doubao", "custom"]);

  function findRecommendedAnswerService(data, draft) {
    const services = data.catalog?.services || [];
    const readingEngine = draft.reading?.primary;
    const preferredReadingSpec = services.find((s) => s.engine === readingEngine && s.roles?.includes("answering"));
    const candidates = [];
    if (preferredReadingSpec) candidates.push(preferredReadingSpec);
    for (const s of services) {
      if (s.roles?.includes("answering") && s.key !== draft.answering.provider && !candidates.some((c) => c.key === s.key)) {
        candidates.push(s);
      }
    }
    return candidates.find((s) => serviceEffectiveState(s.key).configured) || null;
  }

  function buildReadingSection(data, draft, refs) {
    const card = createEl("section", "credential-service api-unified-section");
    card.id = "apiSettingsReadingPanel";
    const head = createEl("div", "credential-service-head");
    const title = createEl("strong");
    title.append(createEl("span", "credential-step-number", "1"), " 看图读题设置");
    const visionServices = (data.catalog?.services || []).filter((spec) => spec.roles?.includes("reading"));
    const activeSpec = visionServices.find((spec) => spec.engine === draft.reading.primary);
    const badgeState = readingBadgeState(data, draft, activeSpec);
    const badge = createEl("span", `api-state ${badgeState.tone}`, badgeState.text);
    head.append(title, badge);
    card.append(head);

    card.append(createEl("p", "credential-purpose", "切完题后负责识别图片里的文字和公式；推荐魔搭（每日免费）或 MiniMax。"));

    const fieldset = createEl("fieldset", "provider-picker");
    fieldset.id = "credentialVisionPicker";
    fieldset.append(createEl("legend", "field-legend", "选择看图读题服务"));

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
      const stateEm = createEl("em", "provider-option-state", st.label);
      refs.visionOptionStates.set(spec.key, stateEm);
      span.append(stateEm);
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

    refs.readingBadge = badge;
    refs.readingActiveSpec = activeSpec || null;

    if (activeSpec) {
      const currentModel = draft.reading.models[activeSpec.key] ?? activeSpec.reading_default_model ?? "";
      const advanced = createEl("details", "settings-advanced api-answer-advanced");
      wireDetailsPersistence(
        advanced,
        `reading-advanced:${activeSpec.key}`,
        Boolean(currentModel && currentModel !== activeSpec.reading_default_model)
      );
      advanced.append(createEl("summary", "", `模型与档位参数（当前：${currentModel || "默认"} · 一般不用改）`));

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
      input.value = currentModel;
      input.placeholder = activeSpec.reading_default_model || "填写视觉模型 ID";
      input.disabled = isMutating();
      input.addEventListener("input", () => {
        draft.reading.models[activeSpec.key] = input.value.trim();
        syncDirtyIndicator();
      });
      bindEnterToSave(input);
      modelRow.append(input, datalist);
      advanced.append(modelRow);

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
        advanced.append(planLabel);
      }
      card.append(advanced);

      // 读题连接测试（明确确认才发送合成图；未填密钥时先引导去填密钥）
      const hasKey = serviceEffectiveState(activeSpec.key).configured;
      const probeBox = createEl("div", "credential-connection-test");
      const missingBar = buildMissingKeyBar(activeSpec.key, activeSpec.label);
      missingBar.hidden = hasKey;
      probeBox.append(missingBar);

      const consentLabel = createEl("label", "credential-consent");
      const consentBox = createEl("input");
      consentBox.type = "checkbox";
      consentBox.checked = draft.reading.testConsent;
      consentBox.disabled = isMutating() || !hasKey;
      consentBox.addEventListener("change", () => {
        draft.reading.testConsent = consentBox.checked;
        syncDirtyIndicator();
      });
      consentLabel.append(consentBox, createEl("span", "", `我同意测试 ${activeSpec.label} 看图读题连接（发送一张本地合成图，可能消耗极少额度）`));

      const actions = createEl("div", "credential-connection-actions");
      const dirty = hasUnsavedChanges();
      const testBtn = createEl(
        "button",
        "button",
        state.testingTarget === `reading:${activeSpec.key}`
          ? "正在测试…"
          : dirty
            ? `保存并测试 ${activeSpec.label} 看图连接`
            : `测试 ${activeSpec.label} 看图连接`
      );
      testBtn.type = "button";
      testBtn.disabled = isMutating() || !hasKey || !draft.reading.testConsent;
      testBtn.addEventListener("click", () => {
        void runConnectionTest("reading", activeSpec.key);
      });
      const resultSpan = createEl("span", `settings-save-result ${draft.reading.testTone || ""}`, draft.reading.testResult || "");
      actions.append(testBtn, resultSpan);
      probeBox.append(consentLabel, actions);
      card.append(probeBox);
      refs.readingProbe = {
        serviceKey: activeSpec.key,
        label: activeSpec.label,
        missingBar,
        consentBox,
        testBtn,
      };
    } else {
      card.append(createEl("p", "settings-footnote", "当前选择暂不调用云端看图模型：切题后直接原图审核或交由 AI 助手读取。"));
    }
    return card;
  }

  function renderCredentialCard(spec, draft, refs) {
    const cred = draft.credentials[spec.key];
    const isReadingActive = Boolean(spec.engine && draft.reading.primary === spec.engine);
    const isAnsweringActive = draft.answering.provider === spec.key;
    const isActive = isReadingActive || isAnsweringActive;
    const card = createEl("article", `credential-service api-service-card${isActive ? " is-active-service" : ""}`);
    card.dataset.service = spec.key;

    const cardHead = createEl("div", "credential-service-head");
    const nameWrap = createEl("div", "api-service-title");
    const titleLabel = spec.key === "mineru" ? `${spec.label}（可选 · 仅整卷自动切题）` : spec.label;
    nameWrap.append(createEl("strong", "", titleLabel));
    for (const role of spec.roles || []) {
      nameWrap.append(createEl("span", "api-role-pill", ROLE_LABELS[role] || role));
    }
    if (isReadingActive) nameWrap.append(createEl("span", "api-active-pill", "当前读题"));
    if (isAnsweringActive) nameWrap.append(createEl("span", "api-active-pill", "当前答题"));
    const st = serviceEffectiveState(spec.key);
    const badgeTone = st.pending ? "is-pending" : st.configured ? "is-ready" : "";
    const stateBadge = createEl("span", `api-state ${badgeTone}`, st.label);
    cardHead.append(nameWrap, stateBadge);
    card.append(cardHead);
    refs.serviceCards.set(spec.key, { card, badge: stateBadge, spec });

    if (spec.note) {
      card.append(createEl("p", "credential-purpose", spec.note));
    }

    // 已保存密钥行（按 count 渲染 0..N 行；子节点顺序受 test_credential_saved_keys.js 契约保护）
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

    // 输入新密钥（支持本地显隐切换，绝不触碰后端明文接口）
    const inputId = `apiKeyInput-${spec.key}`;
    const labelRow = createEl("label");
    labelRow.htmlFor = inputId;
    labelRow.append(createEl("span", "", spec.input_label || "填写或更新密钥（留空就不改）"));
    if (spec.signup) {
      const link = createEl("a", "api-signup-link", "去官网申请");
      link.href = spec.signup;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      labelRow.append(" · ", link);
    }
    const isInputVisible = state.visibleInputs.has(spec.key);
    const inputWrap = createEl("div", "api-secret-input-wrap");
    const input = createEl("input");
    input.id = inputId;
    input.type = isInputVisible ? "text" : "password";
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
    bindEnterToSave(input);

    const visToggleBtn = createEl("button", "button small api-secret-toggle", isInputVisible ? "隐藏" : "显示");
    visToggleBtn.type = "button";
    visToggleBtn.disabled = isMutating();
    visToggleBtn.setAttribute("aria-pressed", isInputVisible ? "true" : "false");
    visToggleBtn.setAttribute("aria-label", `${isInputVisible ? "隐藏" : "显示"}正在输入的 ${spec.label} 密钥`);
    visToggleBtn.addEventListener("click", () => {
      const nextVisible = !state.visibleInputs.has(spec.key);
      if (nextVisible) state.visibleInputs.add(spec.key);
      else state.visibleInputs.delete(spec.key);
      input.type = nextVisible ? "text" : "password";
      visToggleBtn.textContent = nextVisible ? "隐藏" : "显示";
      visToggleBtn.setAttribute("aria-pressed", nextVisible ? "true" : "false");
    });
    inputWrap.append(input, visToggleBtn);
    refs.inputRows.set(spec.key, { input, toggleBtn: visToggleBtn });
    card.append(labelRow, inputWrap);

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

    return card;
  }

  function buildCredentialsSection(data, draft, refs) {
    const section = createEl("section", "credential-service api-unified-section");
    section.id = "apiSettingsCredentialPanel";
    const head = createEl("div", "credential-service-head");
    const title = createEl("strong");
    title.append(createEl("span", "credential-step-number", "2"), " 填写服务密钥（填一次全站共用）");
    const totalCount = Object.keys(draft.credentials).reduce((acc, key) => {
      const st = serviceEffectiveState(key);
      return acc + (st.configured ? st.count : 0);
    }, 0);
    const totalEl = createEl("span", "credential-saved-total", `已配 ${totalCount} 个账号`);
    totalEl.id = "credentialSavedTotal";
    refs.totalEl = totalEl;
    head.append(title, totalEl);
    section.append(head);
    section.append(createEl("p", "credential-storage-note", "密钥全部加密保存在这台电脑里；读题和题库生成答案/标签同一家只填一次。"));

    const services = data.catalog?.services || [];
    const primaryServices = services.filter((spec) => !ANSWER_ONLY_KEY_SERVICES.has(spec.key));
    const secondaryServices = services.filter((spec) => ANSWER_ONLY_KEY_SERVICES.has(spec.key));

    const list = createEl("div", "api-credential-grid");
    for (const spec of primaryServices) {
      list.append(renderCredentialCard(spec, draft, refs));
    }
    section.append(list);

    if (secondaryServices.length > 0) {
      const more = createEl("details", "api-more-credentials");
      const anySecondaryUsed = secondaryServices.some((spec) => {
        const st = serviceEffectiveState(spec.key);
        return st.configured || Boolean(draft.credentials[spec.key]?.input?.trim()) || draft.answering.provider === spec.key;
      });
      wireDetailsPersistence(more, "more-credentials", anySecondaryUsed);
      more.append(createEl("summary", "", "更多仅用于题库答题/标签的独立模型密钥（DeepSeek / 豆包 / 其他自定义，可选）"));
      const secondaryGrid = createEl("div", "api-credential-grid");
      for (const spec of secondaryServices) {
        secondaryGrid.append(renderCredentialCard(spec, draft, refs));
      }
      more.append(secondaryGrid);
      refs.moreCredentialsDetails = more;
      section.append(more);
    }
    return section;
  }

  function buildAnsweringSection(data, draft, refs) {
    const panel = createEl("section", "credential-service api-unified-section");
    panel.id = "credentialAnswerPanel";
    const ansData = data.answering || {};
    const ans = draft.answering;

    const head = createEl("div", "credential-service-head");
    const title = createEl("strong");
    title.append(createEl("span", "credential-step-number", "3"), " 题库生成答案与 AI 标签建议");
    const badgeState = answeringBadgeState(data, draft);
    const badge = createEl("span", `api-state ${badgeState.tone}`, badgeState.text);
    head.append(title, badge);
    panel.append(head);
    refs.answeringBadge = badge;

    // 若当前所选答题服务未配置，但用户已在上方填了其他服务密钥（如 MiniMax / 魔搭 / 硅基流动），给一键沿用按钮
    const activeProviderConfigured = serviceEffectiveState(ans.provider).configured;
    const recommended = !activeProviderConfigured ? findRecommendedAnswerService(data, draft) : null;
    if (recommended) {
      const banner = createEl("div", "api-quick-reuse-banner");
      const bannerText = createEl("span", "", `你已在上方填好「${recommended.label}」密钥，可直接切换使用它来生成题库答案和 AI 标签。`);
      const useBtn = createEl("button", "button small primary", `一键改用 ${recommended.label}`);
      useBtn.type = "button";
      useBtn.disabled = isMutating();
      useBtn.addEventListener("click", () => {
        ans.provider = recommended.key;
        ans.testConsent = false;
        ans.testResult = "";
        renderContent();
      });
      banner.append(bannerText, useBtn);
      panel.append(banner);
    }

    // 功能开关区（主开关 + 缩进子开关）
    const switchesBox = createEl("div", "api-answering-switches");
    const answerSwitch = createSwitchRow(
      "AI 参考答案",
      `开启后可在题库内生成参考答案${typeof ansData.backlog?.missing_answer === "number" ? `（当前缺答案 ${ansData.backlog.missing_answer} 题）` : ""}`,
      ans.features.ai_answer,
      (checked) => {
        ans.features.ai_answer = checked;
        if (!checked) ans.on_intake.answer = false;
        renderContent();
      }
    );
    const intakeSwitch = createSwitchRow(
      "新入库题目自动排入生成参考答案",
      "仅在开启上方「AI 参考答案」时生效",
      ans.on_intake.answer,
      (checked) => {
        ans.on_intake.answer = checked;
        syncDirtyIndicator();
      },
      !ans.features.ai_answer
    );
    intakeSwitch.className = "settings-switch api-sub-switch";
    const kSummary = ansData.knowledge || {};
    const tagSwitch = createSwitchRow(
      "AI 标签建议",
      kSummary.total ? `按正式题库标签目录建议（共 ${kSummary.chapters || 0} 章 ${kSummary.total} 个考点）` : "从正式题库标签目录筛选建议考点，点一下即可采纳",
      ans.features.knowledge_tags,
      (checked) => {
        ans.features.knowledge_tags = checked;
        if (!checked) ans.autoTagBackground = false;
        renderContent();
      }
    );
    const bgTagSwitch = createSwitchRow(
      "无标签题目后台自动慢跑",
      "开启后，后台低优先级并行为还没打标签的题目生成候选建议；你在工作台点单题会立刻插队到最前",
      ans.autoTagBackground,
      (checked) => {
        ans.autoTagBackground = checked;
        syncDirtyIndicator();
      },
      !ans.features.knowledge_tags
    );
    bgTagSwitch.className = "settings-switch api-sub-switch";
    switchesBox.append(answerSwitch, intakeSwitch, tagSwitch, bgTagSwitch);
    panel.append(switchesBox);

    {
      const answerServices = (data.catalog?.answer_order || []).map(
        (key) => (data.catalog?.services || []).find((spec) => spec.key === key)
      ).filter(Boolean);

      const providerRow = createEl("label", "model-id-field");
      providerRow.append(createEl("span", "", "解题与标签使用哪家服务"));
      const select = createEl("select");
      select.disabled = isMutating();
      for (const spec of answerServices) {
        const st = serviceEffectiveState(spec.key);
        const opt = document.createElement("option");
        opt.value = spec.key;
        opt.textContent = `${spec.label}${st.configured ? `（✓ ${st.label}）` : "（未填密钥）"}`;
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
      refs.answeringSelect = select;
      refs.answerServices = answerServices;

      // 共用提示（第 2 步统一管理，未填密钥时提供跳转填写入口）
      const activeKeyState = serviceEffectiveState(ans.provider);
      const activeSpec = answerServices.find((spec) => spec.key === ans.provider);
      const isSharedVision = activeSpec?.roles?.includes("reading");
      const footnoteRow = createEl("div", "api-footnote-row");
      const footnoteText = createEl(
        "p",
        "settings-footnote",
        activeKeyState.configured
          ? `已关联上方第 2 步的 ${activeSpec?.label || ans.provider} 密钥（${activeKeyState.label}${isSharedVision ? " · 与读题全站共用，无需再次输入" : ""}）。`
          : `尚未填写 ${activeSpec?.label || ans.provider} 密钥：请直接在上方第 2 步填写，或改选你已经填好密钥的服务商。`
      );
      const footnoteGotoBtn = createEl("button", "button small", "去填写密钥");
      footnoteGotoBtn.type = "button";
      footnoteGotoBtn.hidden = activeKeyState.configured;
      footnoteGotoBtn.addEventListener("click", () => {
        focusServiceKeyInput(ans.provider);
      });
      footnoteRow.append(footnoteText, footnoteGotoBtn);
      panel.append(footnoteRow);
      refs.answeringFootnote = footnoteText;
      refs.answeringGotoBtn = footnoteGotoBtn;

      const profile = ans.profiles[ans.provider] || {
        model: "",
        base_url: "",
        supports_images: false,
        thinking: true,
      };

      const advanced = createEl("details", "settings-advanced api-answer-advanced");
      wireDetailsPersistence(
        advanced,
        `answering-advanced:${ans.provider}`,
        ans.provider === "custom" || ans.provider === "doubao" || !profile.model
      );
      advanced.append(createEl("summary", "", `高级模型参数（当前模型：${profile.model || "未填写"} · 一般保持默认即可）`));

      const modelField = createEl("label", "model-id-field");
      modelField.append(createEl("span", "", ans.provider === "doubao" ? "火山方舟 Endpoint ID（以 ep- 开头）" : "模型 ID"));
      const ansModelListId = `apiAnswerModels-${ans.provider}`;
      const ansDatalist = createEl("datalist");
      ansDatalist.id = ansModelListId;
      for (const m of ANSWER_SUGGESTED_MODELS[ans.provider] || activeSpec?.reading_suggested_models || []) {
        const opt = document.createElement("option");
        opt.value = m;
        ansDatalist.append(opt);
      }
      const modelInput = createEl("input");
      modelInput.type = "text";
      modelInput.setAttribute("list", ansModelListId);
      modelInput.autocomplete = "off";
      modelInput.spellcheck = false;
      modelInput.value = profile.model;
      modelInput.placeholder = ans.provider === "doubao" ? "例如 ep-20250101xxxxxx-yyyy" : "填写服务商控制台的模型 ID";
      modelInput.disabled = isMutating();
      modelInput.addEventListener("input", () => {
        profile.model = modelInput.value.trim();
        syncDirtyIndicator();
      });
      bindEnterToSave(modelInput);
      modelField.append(modelInput, ansDatalist);
      advanced.append(modelField);

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
      bindEnterToSave(baseInput);
      baseField.append(baseInput);
      advanced.append(baseField);

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
      advanced.append(capsBox);
      panel.append(advanced);

      // 独立解题连接测试（未填密钥时先引导填写，禁止空发请求）
      const probeBox = createEl("div", "credential-connection-test");
      const missingBar = buildMissingKeyBar(ans.provider, activeSpec?.label || ans.provider);
      missingBar.hidden = activeProviderConfigured;
      probeBox.append(missingBar);

      const inlineHint = createEl(
        "p",
        "api-inline-hint",
        "还差一步：确认配置已保存（输入后按 Enter 可直接保存），勾选确认框并点击「测试解题连接」，测通后题库即可生成答案和标签。"
      );
      inlineHint.hidden = !activeProviderConfigured || Boolean(ansData.verified);
      probeBox.append(inlineHint);

      const consentLabel = createEl("label", "credential-consent");
      const consentBox = createEl("input");
      consentBox.type = "checkbox";
      consentBox.checked = ans.testConsent;
      consentBox.disabled = isMutating() || !activeProviderConfigured;
      consentBox.addEventListener("change", () => {
        ans.testConsent = consentBox.checked;
        syncDirtyIndicator();
      });
      consentLabel.append(consentBox, createEl("span", "", "我同意运行一次合成数学连接测试（已保存后可测，可能产生少量费用）"));
      const actions = createEl("div", "credential-connection-actions");
      const dirty = hasUnsavedChanges();
      const testBtn = createEl(
        "button",
        "button",
        state.testingTarget === "answering"
          ? "正在测试…"
          : dirty
            ? "保存并测试解题连接"
            : "测试解题连接"
      );
      testBtn.type = "button";
      testBtn.disabled = isMutating() || !activeProviderConfigured || !ans.testConsent;
      testBtn.addEventListener("click", () => {
        void runConnectionTest("answering", ans.provider);
      });
      const noteText = ans.testResult || (ansData.verified ? `已测通（模型：${ansData.resolved_model || ansData.model}）` : "");
      const resSpan = createEl("span", `settings-save-result ${ans.testTone || (ansData.verified ? "is-success" : "")}`, noteText);
      actions.append(testBtn, resSpan);
      probeBox.append(consentLabel, actions);
      panel.append(probeBox);
      refs.answeringProbe = {
        missingBar,
        inlineHint,
        consentBox,
        testBtn,
      };
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
    if (!state.dom || !state.draft) return;
    const dirty = hasUnsavedChanges();
    const {
      footer,
      saveBtn,
      discardBtn,
      dirtyTag,
      serviceCards,
      totalEl,
      visionOptionStates,
      readingBadge,
      readingActiveSpec,
      readingProbe,
      answeringBadge,
      answeringSelect,
      answerServices,
      answeringFootnote,
      answeringGotoBtn,
      answeringProbe,
    } = state.dom;

    if (serviceCards) {
      for (const [key, entry] of serviceCards.entries()) {
        const st = serviceEffectiveState(key);
        const tone = st.pending ? "is-pending" : st.configured ? "is-ready" : "";
        entry.badge.className = `api-state ${tone}`;
        entry.badge.textContent = st.label;
      }
    }
    if (totalEl) {
      const totalCount = Object.keys(state.draft.credentials).reduce((acc, key) => {
        const st = serviceEffectiveState(key);
        return acc + (st.configured ? st.count : 0);
      }, 0);
      totalEl.textContent = `已配 ${totalCount} 个账号`;
    }
    if (visionOptionStates) {
      for (const [key, stateEm] of visionOptionStates.entries()) {
        stateEm.textContent = serviceEffectiveState(key).label;
      }
    }
    if (readingBadge) {
      const rb = readingBadgeState(state.serverData, state.draft, readingActiveSpec);
      readingBadge.className = `api-state ${rb.tone}`;
      readingBadge.textContent = rb.text;
    }
    if (readingProbe) {
      const hasKey = serviceEffectiveState(readingProbe.serviceKey).configured;
      readingProbe.missingBar.hidden = hasKey;
      readingProbe.consentBox.disabled = isMutating() || !hasKey;
      readingProbe.testBtn.disabled = isMutating() || !hasKey || !state.draft.reading.testConsent;
      readingProbe.testBtn.textContent = state.testingTarget === `reading:${readingProbe.serviceKey}`
        ? "正在测试…"
        : dirty
          ? `保存并测试 ${readingProbe.label} 看图连接`
          : `测试 ${readingProbe.label} 看图连接`;
    }

    const ans = state.draft.answering;
    const ansHasKey = serviceEffectiveState(ans.provider).configured;
    if (answeringBadge) {
      const ab = answeringBadgeState(state.serverData, state.draft);
      answeringBadge.className = `api-state ${ab.tone}`;
      answeringBadge.textContent = ab.text;
    }
    if (answeringSelect && answerServices) {
      const specByKey = new Map(answerServices.map((item) => [item.key, item]));
      for (const opt of answeringSelect.children || []) {
        const spec = specByKey.get(opt.value);
        if (!spec) continue;
        const st = serviceEffectiveState(spec.key);
        opt.textContent = `${spec.label}${st.configured ? `（✓ ${st.label}）` : "（未填密钥）"}`;
      }
    }
    if (answeringFootnote && answerServices) {
      const activeSpec = answerServices.find((spec) => spec.key === ans.provider);
      const activeKeyState = serviceEffectiveState(ans.provider);
      const isSharedVision = activeSpec?.roles?.includes("reading");
      answeringFootnote.textContent = activeKeyState.configured
        ? `已关联上方第 2 步的 ${activeSpec?.label || ans.provider} 密钥（${activeKeyState.label}${isSharedVision ? " · 与读题全站共用，无需再次输入" : ""}）。`
        : `尚未填写 ${activeSpec?.label || ans.provider} 密钥：请直接在上方第 2 步填写，或改选你已经填好密钥的服务商。`;
    }
    if (answeringGotoBtn) {
      answeringGotoBtn.hidden = ansHasKey;
    }
    if (answeringProbe) {
      answeringProbe.missingBar.hidden = ansHasKey;
      answeringProbe.inlineHint.hidden = !ansHasKey || Boolean(state.serverData?.answering?.verified);
      answeringProbe.consentBox.disabled = isMutating() || !ansHasKey;
      answeringProbe.testBtn.disabled = isMutating() || !ansHasKey || !ans.testConsent;
      answeringProbe.testBtn.textContent = state.testingTarget === "answering"
        ? "正在测试…"
        : dirty
          ? "保存并测试解题连接"
          : "测试解题连接";
    }

    if (saveBtn) {
      saveBtn.hidden = !dirty && !state.saving;
      saveBtn.disabled = isMutating();
      saveBtn.textContent = state.saving ? "正在保存…" : "保存更改";
    }
    if (discardBtn) {
      discardBtn.hidden = !dirty || state.saving;
      discardBtn.disabled = isMutating();
    }
    if (dirtyTag) {
      dirtyTag.hidden = !dirty;
      dirtyTag.textContent = dirty ? "有未保存的改动（输入后按 Enter 可直接保存）" : "";
    }
    if (footer) {
      footer.className = `credential-actions${dirty ? " is-dirty" : ""}`;
      footer.hidden = state.mode !== "modal" && !dirty && !state.saving && !state.lastStatusMessage;
    }
  }

  function renderContent() {
    if (!state.host) return;
    const activeEl = typeof document !== "undefined" ? document.activeElement : null;
    const activeId = activeEl && activeEl.id && typeof state.host.contains === "function" && state.host.contains(activeEl)
      ? activeEl.id
      : "";

    const root = createEl("div", "credential-ui api-unified-root");
    root.addEventListener("keydown", (event) => {
      if (!event || event.isComposing || event.keyCode === 229) return;
      const key = String(event.key || "").toLowerCase();
      if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && key === "s") {
        if (typeof event.preventDefault === "function") event.preventDefault();
        if (!isMutating() && hasUnsavedChanges()) {
          void save();
        }
      }
    });

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

    const refs = {
      visionOptionStates: new Map(),
      serviceCards: new Map(),
      inputRows: new Map(),
      readingBadge: null,
      readingActiveSpec: null,
      readingProbe: null,
      totalEl: null,
      moreCredentialsDetails: null,
      answeringBadge: null,
      answeringSelect: null,
      answerServices: null,
      answeringFootnote: null,
      answeringGotoBtn: null,
      answeringProbe: null,
    };

    const body = createEl("div", "credential-body");
    body.append(
      buildReadingSection(state.serverData, state.draft, refs),
      buildCredentialsSection(state.serverData, state.draft, refs),
      buildAnsweringSection(state.serverData, state.draft, refs)
    );

    const footer = createEl("footer", "credential-actions");
    const statusMsg = createEl("span", `settings-save-result ${state.lastStatusTone || ""}`, state.lastStatusMessage || "");
    statusMsg.id = "credentialResult";
    statusMsg.setAttribute("role", "status");
    const dirtyTag = createEl("span", "api-dirty-badge");
    dirtyTag.hidden = true;
    const discardBtn = createEl("button", "button quiet", "放弃更改");
    discardBtn.id = "credentialDiscard";
    discardBtn.type = "button";
    discardBtn.hidden = true;
    discardBtn.addEventListener("click", () => {
      void discard();
    });
    const saveBtn = createEl("button", "button primary", "保存更改");
    saveBtn.id = "credentialSave";
    saveBtn.type = "button";
    saveBtn.hidden = true;
    saveBtn.addEventListener("click", () => {
      void save();
    });

    footer.append(statusMsg, dirtyTag, discardBtn, saveBtn);
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
    state.dom = { ...refs, footer, saveBtn, discardBtn, statusMsg, dirtyTag };
    syncDirtyIndicator();

    if (activeId) {
      const restored = document.getElementById(activeId);
      if (restored && !restored.disabled && typeof restored.focus === "function") {
        restored.focus({ preventScroll: true });
      }
    }
  }

  async function loadConfig({ preserveDraftIfDirty = false } = {}) {
    const epoch = ++state.loadEpoch;
    state.loading = true;
    renderContent();
    try {
      const data = await requestJson("/api/settings/api-config");
      if (epoch !== state.loadEpoch) return null;
      state.serverData = data;
      updateStepTracks(data);
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
      const prevBg = Boolean(
        Array.isArray(state.serverData?.features)
          ? state.serverData.features.find((item) => item && item.key === "auto_tag_background")?.enabled
          : false
      );
      const nextBg = Boolean(state.draft.answering.autoTagBackground);
      const payload = buildSavePayload();
      const updated = await requestJson("/api/settings/api-config", {
        method: "POST",
        body: payload,
      });
      if (nextBg !== prevBg) {
        const featRes = await requestJson("/api/settings/features", {
          method: "POST",
          body: { features: { auto_tag_background: nextBg } },
        });
        if (featRes && Array.isArray(featRes.features)) {
          updated.features = featRes.features;
        }
      }
      for (const [service, op] of Object.entries(payload.credentials || {})) {
        if (op.action === "replace" || op.action === "clear") {
          state.readingVerified.delete(service);
        }
      }
      const savedModels = payload.reading?.models || {};
      for (const [service, verifiedModel] of Array.from(state.readingVerified.entries())) {
        if (savedModels[service] !== verifiedModel) {
          state.readingVerified.delete(service);
        }
      }
      hideSecrets();
      state.serverData = updated;
      state.draft = cloneDraftFromServer(updated);
      state.baselineSignature = draftDirtySignature(state.draft);
      state.lastStatusMessage = updated.message || "API 配置已保存";
      state.lastStatusTone = "is-success";
      updateStepTracks(updated);
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
      const ok = await save();
      if (!ok) return;
    }
    state.testingTarget = target === "reading" ? `reading:${service}` : "answering";
    renderContent();
    try {
      const res = await requestJson("/api/settings/api-config/test", {
        method: "POST",
        body: target === "reading" ? { target: "reading", service, confirm: true } : { target: "answering", confirm: true },
        timeoutMs: TEST_REQUEST_TIMEOUT_MS,
      });
      if (res.state) {
        state.serverData = res.state;
      }
      const msg = res.message || (res.ok ? "连接测试通过" : "连接测试未通过");
      if (target === "reading") {
        if (res.ok) {
          state.readingVerified.set(service, res.model || state.draft.reading.models[service] || "");
        } else {
          state.readingVerified.delete(service);
        }
        state.draft.reading.testResult = msg;
        state.draft.reading.testTone = res.ok ? "is-success" : "is-error";
      } else {
        state.draft.answering.testResult = msg;
        state.draft.answering.testTone = res.ok ? "is-success" : "is-error";
      }
      if (state.serverData) updateStepTracks(state.serverData);
      showToast(msg, res.ok ? "success" : "error");
    } catch (error) {
      if (error && error.payload && error.payload.state) {
        state.serverData = error.payload.state;
      }
      const msg = error.message || "连接测试失败";
      if (target === "reading") {
        state.readingVerified.delete(service);
        state.draft.reading.testResult = msg;
        state.draft.reading.testTone = "is-error";
      } else {
        state.draft.answering.testResult = msg;
        state.draft.answering.testTone = "is-error";
      }
      if (state.serverData) updateStepTracks(state.serverData);
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
