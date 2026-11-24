/*
 * 录题界面与正式题库共用的全屏三区「编辑」工作台辅助内核。
 * 提供精简三分栏顶栏、题干工具条（填空横线 + 表格）、动态 A–H 选项面板、
 * 实时预览光标同步与「AI 推荐答案」交互组件。
 */
((root, factory) => {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.QBEditorHelpers = api;
})(typeof window === "undefined" ? globalThis : window, () => {
  "use strict";

  const OPTION_KEYS = ["A", "B", "C", "D", "E", "F", "G", "H"];

  const makeEl = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  };

  /**
   * 题干纯文本表格插入与扩行扩列纯函数
   */
  const QBTableText = (() => {
    const isRow = (line) => line.trim().startsWith("|") && (line.match(/\|/g) || []).length >= 2;

    function insert(value, cursor) {
      const text = String(value ?? "");
      const at = Math.max(0, Math.min(text.length, Number(cursor) || 0));
      const before = text.slice(0, at);
      const after = text.slice(at);
      const lead = before && !before.endsWith("\n") ? "\n" : "";
      const tail = after && !after.startsWith("\n") ? "\n" : "";
      const table = "|  |  |  |\n|---|---|---|\n|  |  |  |\n|  |  |  |";
      return { value: `${before}${lead}${table}${tail}${after}`, cursor: before.length + lead.length + 2 };
    }

    function around(value, cursor) {
      const lines = String(value ?? "").split("\n");
      let offset = 0;
      let line = 0;
      for (; line < lines.length; line += 1) {
        if (cursor <= offset + lines[line].length) break;
        offset += lines[line].length + 1;
      }
      let index = Math.min(line, lines.length - 1);
      if (!isRow(lines[index] || "") && index > 0 && isRow(lines[index - 1])) index -= 1;
      if (!isRow(lines[index] || "")) return null;
      let first = index;
      let last = index;
      while (first > 0 && isRow(lines[first - 1])) first -= 1;
      while (last + 1 < lines.length && isRow(lines[last + 1])) last += 1;
      if (last === first) return null;
      return { lines, first, last };
    }

    function grow(value, cursor, what) {
      const table = around(value, cursor);
      if (!table) return null;
      const { lines, first, last } = table;
      const cells = (line) => {
        const text = line.trim().replace(/^\|/, "").replace(/\|$/, "");
        return text.split("|").length;
      };
      const width = Math.max(...lines.slice(first, last + 1).map(cells));
      let focus;
      if (what === "row") {
        lines.splice(last + 1, 0, `|${"  |".repeat(width)}`);
        focus = last + 1;
      } else {
        for (let index = first; index <= last; index += 1) {
          const separator = /^\s*\|?(\s*:?-{3,}:?\s*\|)+\s*$/.test(lines[index]);
          lines[index] = `${lines[index].replace(/\s*$/, "").replace(/\|?$/, "|")}${separator ? "---|" : "  |"}`;
        }
        focus = first;
      }
      const next = lines.join("\n");
      const cursorAt = lines.slice(0, focus).reduce((sum, line) => sum + line.length + 1, 0) + 2;
      return { value: next, cursor: Math.min(cursorAt, next.length) };
    }

    return { insert, grow };
  })();

  /**
   * 在题干当前光标或选区位置插入标准填空横线
   * @param {string} value 原题干字符串
   * @param {number} start 选区起点
   * @param {number} end 选区终点
   * @returns {{ value: string, cursor: number }} 新题干与新光标位置
   */
  function insertStemBlank(value, start = 0, end = start) {
    const text = String(value ?? "");
    const from = Math.max(0, Math.min(text.length, Number(start) || 0));
    const to = Math.max(from, Math.min(text.length, Number(end ?? from) || from));
    const blank = "______";
    return { value: text.slice(0, from) + blank + text.slice(to), cursor: from + blank.length };
  }

  /**
   * 根据内容自适应调整多行输入框高度，避免频繁出现嵌套小滚动条
   * @param {HTMLTextAreaElement} textarea 文本框元素
   * @param {object} [opts] 最小行数与最大像素高度限制
   */
  function fitAutoHeight(textarea, opts = {}) {
    if (!textarea) return;
    const minRows = opts.minRows || 2;
    const maxHeightPx = opts.maxHeightPx || Math.round((typeof window !== "undefined" ? window.innerHeight : 900) * 0.4);
    textarea.rows = minRows;
    textarea.style.height = "auto";
    const measured = Number(textarea.scrollHeight) || 0;
    if (measured > 0) textarea.style.height = `${Math.min(measured + 2, maxHeightPx)}px`;
  }

  /**
   * 构造统一精简三分栏顶栏（左返回+状态徽标，中分段切题器，右题型+保存）
   * @param {object} opts 顶栏初始化参数与回调
   */
  function buildEditorBar(opts = {}) {
    const createEl = opts.createEl || makeEl;
    const bar = createEl("div", "editor-bar");

    const left = createEl("div", "editor-bar-left");
    const backButton = createEl("button", "button quiet editor-bar-back", opts.backLabel || "← 返回");
    backButton.type = "button";
    backButton.title = opts.backTitle || "Esc 返回；有未保存改动会先确认";
    if (typeof opts.onBack === "function") backButton.addEventListener("click", opts.onBack);

    const badgeEl = createEl("span", "editor-bar-badge");
    badgeEl.hidden = true;
    const setBadge = (text = "", tone = "neutral") => {
      const value = String(text || "").trim();
      badgeEl.textContent = value;
      badgeEl.hidden = !value;
      badgeEl.className = value ? `editor-bar-badge is-${tone || "neutral"}` : "editor-bar-badge";
    };
    if (opts.badgeText) setBadge(opts.badgeText, opts.badgeTone);
    left.append(backButton, badgeEl);

    const center = createEl("div", "editor-bar-center");
    const stepper = createEl("div", "editor-bar-stepper");
    const prevButton = createEl("button", "button small editor-bar-nav", "‹ 上一题");
    prevButton.type = "button";
    const place = createEl("div", "editor-bar-place");
    const countEl = createEl("span", "editor-bar-count", opts.countText || "");
    place.append(countEl);

    let foldedEl = null;
    const setFolded = (foldedCount = 0) => {
      const n = Number(foldedCount) || 0;
      if (!n) {
        if (foldedEl) {
          foldedEl.remove();
          foldedEl = null;
        }
        return;
      }
      if (!foldedEl) {
        foldedEl = createEl("span", "editor-bar-folded-pill");
        place.append(foldedEl);
      }
      foldedEl.textContent = `已折叠 ${n}`;
      foldedEl.title = `另有 ${n} 题已通过且折叠收起，连续切题会跳过它们`;
    };
    setFolded(opts.foldedCount || 0);

    const nextButton = createEl("button", "button small editor-bar-nav", "下一题 ›");
    nextButton.type = "button";
    if (typeof opts.onPrev === "function") prevButton.addEventListener("click", opts.onPrev);
    if (typeof opts.onNext === "function") nextButton.addEventListener("click", opts.onNext);
    prevButton.disabled = Boolean(opts.prevDisabled);
    nextButton.disabled = Boolean(opts.nextDisabled);
    stepper.append(prevButton, place, nextButton);
    center.append(stepper);

    const right = createEl("div", "editor-bar-right");
    const typeSelect = opts.typeSelect || createEl("select");
    const typeField = createEl("label", "editor-bar-type");
    typeField.append(createEl("span", "", "题型"), typeSelect);

    const saveButton = createEl("button", "button primary editor-bar-save", opts.saveLabel || "保存");
    saveButton.type = "button";
    saveButton.title = opts.saveTitle || "Ctrl+Enter / Ctrl+S 保存 · Esc 返回";
    if (typeof opts.onSave === "function") saveButton.addEventListener("click", opts.onSave);

    right.append(typeField, saveButton);
    bar.append(left, center, right);

    return {
      bar,
      left,
      center,
      right,
      backButton,
      badgeEl,
      setBadge,
      prevButton,
      nextButton,
      countEl,
      place,
      setFolded,
      typeField,
      typeSelect,
      saveButton
    };
  }

  /**
   * 构造题干快捷工具栏：支持「插入填空线（____）」与纯文本表格插入/加行/加列
   * @param {object} opts 关联输入框、题型选择与提示回调
   */
  function buildStemToolbar({ stemInput, typeSelect, onToast, afterChange, createEl = makeEl }) {
    const bar = createEl("div", "table-tools stem-tools");

    const applyEdit = (fn) => {
      if (!stemInput || stemInput.disabled) return;
      const start = stemInput.selectionStart ?? stemInput.value.length;
      const end = stemInput.selectionEnd ?? start;
      const next = fn(stemInput.value, start, end);
      if (!next) return;
      stemInput.value = next.value;
      stemInput.focus();
      if (typeof stemInput.setSelectionRange === "function") {
        stemInput.setSelectionRange(next.cursor, next.cursor);
      }
      stemInput.dispatchEvent(new Event("input", { bubbles: true }));
      afterChange?.();
    };

    const blankBtn = createEl("button", "button small stem-blank-tool", "插入填空线 ____");
    blankBtn.type = "button";
    blankBtn.title = "在题干光标处插入标准填空下划线 ______";
    blankBtn.addEventListener("click", () => applyEdit((val, s, e) => insertStemBlank(val, s, e)));

    const insertTableBtn = createEl("button", "button small", "插入表格");
    insertTableBtn.type = "button";
    insertTableBtn.addEventListener("click", () => applyEdit((val, s) => QBTableText.insert(val, s)));

    const addRowBtn = createEl("button", "button small", "表格加一行");
    addRowBtn.type = "button";
    addRowBtn.addEventListener("click", () => applyEdit((val, s) => {
      const next = QBTableText.grow(val, s, "row");
      if (!next) onToast?.("先把光标放在要加行的表格里", "error");
      return next;
    }));

    const addColBtn = createEl("button", "button small", "表格加一列");
    addColBtn.type = "button";
    addColBtn.addEventListener("click", () => applyEdit((val, s) => {
      const next = QBTableText.grow(val, s, "col");
      if (!next) onToast?.("先把光标放在要加列的表格里", "error");
      return next;
    }));

    const hint = createEl("small", "hint", "填空线可直接点“插入填空线”；表格每行以 | 分隔，第二行 |---| 为表头");

    const syncTypeState = () => {
      const isBlank = typeSelect?.value === "fill_blank";
      blankBtn.classList.toggle("is-recommended", isBlank);
    };
    typeSelect?.addEventListener("change", syncTypeState);
    syncTypeState();

    const setDisabled = (disabled) => {
      [blankBtn, insertTableBtn, addRowBtn, addColBtn].forEach((btn) => { btn.disabled = Boolean(disabled); });
    };

    bar.append(blankBtn, insertTableBtn, addRowBtn, addColBtn, hint);
    return { element: bar, blankButton: blankBtn, syncTypeState, setDisabled };
  }

  /**
   * 构造支持按需扩容到 A–H 的选择题动态选项编辑器
   * @param {object} opts 选项上限、初始值、配图占槽信息及回调
   */
  function buildDynamicOptionEditor(opts = {}) {
    const createEl = opts.createEl || makeEl;
    const keys = Array.isArray(opts.optionKeys) && opts.optionKeys.length ? opts.optionKeys : OPTION_KEYS;
    const initialOptions = opts.initialOptions && typeof opts.initialOptions === "object" ? opts.initialOptions : {};
    const initialFigures = Array.isArray(opts.initialFigures) ? opts.initialFigures : [];

    const computeVisibleCount = (optsObj, figsList) => {
      let count = Math.min(4, keys.length);
      for (let index = keys.length - 1; index >= 4; index -= 1) {
        const key = keys[index];
        const hasText = String(optsObj?.[key] ?? "").trim().length > 0;
        const hasFigure = (Array.isArray(figsList) ? figsList : []).some((figure) => !figure?.removed && figure?.slot === key);
        if (hasText || hasFigure) {
          count = index + 1;
          break;
        }
      }
      return count;
    };

    // 初始展示范围：至少 A–D，有已有内容或配图占位的则延伸到最高出现的那一项
    let visibleCount = computeVisibleCount(initialOptions, initialFigures);

    const container = createEl("div", "option-inputs");
    const list = createEl("div", "option-rows");
    const addRow = createEl("div", "option-add-row");
    const addButton = createEl("button", "button small option-add-btn", "+ 添加选项");
    addButton.type = "button";
    const addHint = createEl("small", "hint", "默认显示 A–D，按需补充 E–H");
    addRow.append(addButton, addHint);
    container.append(list, addRow);

    const inputs = {};
    const rows = {};
    let lockedFigures = initialFigures;
    let isDisabled = Boolean(opts.disabled);

    const isSlotLockedByFigure = (key) => lockedFigures.some((fig) => !fig?.removed && fig?.slot === key);

    const syncAddButton = () => {
      if (visibleCount >= keys.length) {
        addRow.hidden = true;
        return;
      }
      addRow.hidden = false;
      const nextKey = keys[visibleCount];
      addButton.textContent = `+ 添加选项（${nextKey}）`;
      addButton.disabled = isDisabled;
    };

    const syncRows = () => {
      keys.forEach((key, index) => {
        const row = rows[key];
        if (!row) return;
        const shown = index < visibleCount;
        row.hidden = !shown;
        const removeBtn = row.querySelector(".option-remove-btn");
        if (removeBtn) {
          removeBtn.hidden = index < 4 || isSlotLockedByFigure(key) || isDisabled;
        }
      });
      syncAddButton();
    };

    keys.forEach((key, index) => {
      const row = createEl("label", "field inline option-row");
      if (index >= 4) row.classList.add("optional");
      const input = createEl(opts.multiline ? "textarea" : "input");
      if (opts.multiline) {
        input.rows = 1;
      }
      input.value = String(initialOptions[key] ?? "");
      input.spellcheck = false;
      if (opts.maxLength) input.maxLength = opts.maxLength;
      if (index >= 4) input.placeholder = `填写 ${key} 选项内容（不需要时留空或移除）`;
      inputs[key] = input;
      rows[key] = row;

      row.append(createEl("span", "option-key-label", key), input);
      if (index >= 4) {
        const removeBtn = createEl("button", "button quiet small option-remove-btn", "×");
        removeBtn.type = "button";
        removeBtn.title = `清空并收起选项 ${key}`;
        removeBtn.addEventListener("click", (event) => {
          event.preventDefault();
          if (isDisabled) return;
          input.value = "";
          if (index + 1 === visibleCount) {
            while (visibleCount > 4) {
              const lastKey = keys[visibleCount - 1];
              if (String(inputs[lastKey].value || "").trim() || isSlotLockedByFigure(lastKey)) break;
              visibleCount -= 1;
            }
          }
          syncRows();
          input.dispatchEvent(new Event("input", { bubbles: true }));
          opts.onRemove?.(key);
        });
        row.append(removeBtn);
      }
      list.append(row);
    });

    addButton.addEventListener("click", () => {
      if (isDisabled || visibleCount >= keys.length) return;
      const nextKey = keys[visibleCount];
      visibleCount += 1;
      syncRows();
      const target = inputs[nextKey];
      target?.focus();
      opts.onAdd?.(nextKey, target);
    });

    const ensureKeyVisible = (key) => {
      const idx = keys.indexOf(key);
      if (idx >= visibleCount) {
        visibleCount = idx + 1;
        syncRows();
      }
    };

    const setDisabled = (disabled) => {
      isDisabled = Boolean(disabled);
      keys.forEach((key) => { inputs[key].disabled = isDisabled; });
      syncRows();
    };

    const updateFigures = (figures) => {
      lockedFigures = Array.isArray(figures) ? figures : [];
      for (let i = keys.length - 1; i >= 4; i -= 1) {
        if (isSlotLockedByFigure(keys[i]) && visibleCount < i + 1) {
          visibleCount = i + 1;
        }
      }
      syncRows();
    };

    const collectValues = () => Object.fromEntries(
      keys
        .map((key) => [key, String(inputs[key]?.value ?? "")])
        .filter(([, value]) => value.trim().length > 0)
    );

    const hasAnyValue = () => keys.some((key) => String(inputs[key]?.value ?? "").trim().length > 0);

    const syncVisibility = (questionType, figures = lockedFigures) => {
      const activeFigs = Array.isArray(figures) ? figures : [];
      const isChoice = questionType === "single_choice" || questionType === "multiple_choice";
      const hasOptFig = activeFigs.some((fig) => !fig?.removed && keys.includes(fig?.slot));
      container.hidden = !isChoice && !hasAnyValue() && !hasOptFig;
      if (!container.hidden) updateFigures(activeFigs);
    };

    const setValues = (nextOptions = {}, nextFigures = []) => {
      const optsMap = nextOptions && typeof nextOptions === "object" ? nextOptions : {};
      lockedFigures = Array.isArray(nextFigures) ? nextFigures : [];
      keys.forEach((key) => {
        if (inputs[key]) inputs[key].value = String(optsMap[key] ?? "");
      });
      visibleCount = computeVisibleCount(optsMap, lockedFigures);
      syncRows();
    };

    syncRows();
    if (typeof opts.onInput === "function") {
      container.addEventListener("input", opts.onInput);
    }

    return {
      container,
      inputs,
      rows,
      addButton,
      ensureKeyVisible,
      setDisabled,
      setValues,
      updateFigures,
      collectValues,
      hasAnyValue,
      syncVisibility,
      getVisibleKeys: () => keys.slice(0, visibleCount)
    };
  }

  /**
   * 绑定输入框与右侧实时预览区光标/选区联动高亮
   * @param {object} opts 预览节点、提示条与输入源获取器
   */
  function bindPreviewCursorSync({ R, preview, positionHint, fieldLabel, getInputsMap, initialInput }) {
    let composing = false;
    let frame = 0;
    let activeInput = initialInput || null;
    const bound = new WeakSet();

    const showPosition = () => {
      const inputsMap = typeof getInputsMap === "function" ? getInputsMap() : getInputsMap;
      const input = activeInput;
      if (!input || !inputsMap?.has?.(input)) return;
      const field = inputsMap.get(input);
      const shown = R.previewSelection(preview, {
        field,
        start: input.selectionStart ?? 0,
        end: input.selectionEnd ?? 0
      });
      const detail = composing ? "正在输入，预览会跟随输入内容更新。"
        : shown?.kind === "math" ? (shown.automatic
          ? "蓝框对应正在修改的数字或数学内容；实际输入位置请看输入框光标。"
          : shown.exact
          ? "绿色符号对应光标附近或所选范围，蓝框是这条公式；实际插入位置请看输入框光标。"
          : "蓝框是正在修改的公式；公式内的位置请看输入框光标。$…$ 和反斜杠是公式写法。")
        : shown?.kind === "table" ? "正在修改表格；分隔线和 | 用来排表格，输入框保留原写法。"
        : shown?.kind === "blank" ? "正在修改这处填空；输入框保留原来的括号或下划线。"
        : shown?.collapsed ? "绿线是当前输入位置。选中文字时，预览会标出对应范围。"
        : "浅绿色是当前选中的文字；替换后预览会更新。";
      if (positionHint) positionHint.textContent = `${fieldLabel(field)} · ${detail}`;
      preview.dataset.activeField = field;

      const symbol = Array.from(preview.querySelectorAll(".qb-preview-active-symbol [style]"))
        .find((node) => node.style.color === "rgb(31, 107, 95)" || node.style.color === "#1f6b5f");
      const target = symbol || preview.querySelector(
        ".qb-preview-caret, .qb-preview-selection, .qb-preview-active-formula, .qb-preview-active-region"
      );
      if (target) {
        const bounds = preview.getBoundingClientRect();
        const rect = target.getBoundingClientRect();
        if (preview.scrollHeight > preview.clientHeight) {
          if (rect.top < bounds.top + 10) preview.scrollTop -= bounds.top + 10 - rect.top;
          else if (rect.bottom > bounds.bottom - 10 && rect.height < preview.clientHeight - 20) {
            preview.scrollTop += rect.bottom - bounds.bottom + 10;
          }
        }
        if (preview.scrollWidth > preview.clientWidth) {
          if (rect.left < bounds.left + 10) preview.scrollLeft -= bounds.left + 10 - rect.left;
          else if (rect.right > bounds.right - 10 && rect.width < preview.clientWidth - 20) {
            preview.scrollLeft += rect.right - bounds.right + 10;
          }
        }
      }
    };

    const scheduleUpdate = (event) => {
      const inputsMap = typeof getInputsMap === "function" ? getInputsMap() : getInputsMap;
      if (event?.target && inputsMap?.has?.(event.target)) activeInput = event.target;
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        showPosition();
      });
    };

    const attachInput = (input) => {
      if (!input || bound.has(input)) return;
      bound.add(input);
      ["focus", "click", "select", "keyup"].forEach((name) => input.addEventListener(name, scheduleUpdate));
      input.addEventListener("compositionstart", (event) => { composing = true; scheduleUpdate(event); });
      input.addEventListener("compositionend", (event) => { composing = false; scheduleUpdate(event); });
    };

    const initialMap = typeof getInputsMap === "function" ? getInputsMap() : getInputsMap;
    initialMap?.forEach((_, input) => attachInput(input));

    const selectionChanged = () => {
      const inputsMap = typeof getInputsMap === "function" ? getInputsMap() : getInputsMap;
      if (inputsMap?.has?.(document.activeElement)) scheduleUpdate({ target: document.activeElement });
    };
    document.addEventListener("selectionchange", selectionChanged);

    return {
      attachInput,
      setActiveInput: (input) => { activeInput = input; },
      showPosition,
      updatePosition: scheduleUpdate,
      teardown: () => {
        cancelAnimationFrame(frame);
        document.removeEventListener("selectionchange", selectionChanged);
      }
    };
  }

  /**
   * 构造「✨ AI 推荐答案」交互组件，支持空框直填标为初稿，非空时预览确认再覆盖
   * @param {object} opts 接口地址、草稿采集器与结果应用回调
   */
  function createAiRecommendWidget(opts = {}) {
    const createEl = opts.createEl || makeEl;
    const button = createEl("button", "button small ai-recommend-btn", "✨ AI 推荐答案");
    button.type = "button";
    button.title = "仅本次按当前题面与配图调用已配置的答题模型，不自动保存、不更改全局开关";

    const status = createEl("div", "ai-recommend-status");
    status.hidden = true;

    const previewBox = createEl("div", "ai-recommend-preview");
    previewBox.hidden = true;

    let controller = null;
    let busy = false;
    let pendingDraft = null;

    const clearFeedback = () => {
      status.replaceChildren();
      status.hidden = true;
      previewBox.replaceChildren();
      previewBox.hidden = true;
      pendingDraft = null;
    };

    const showNotice = (message, { settingsUrl = "/settings#ai", isError = true } = {}) => {
      status.replaceChildren();
      status.hidden = false;
      status.className = isError ? "ai-recommend-status is-error" : "ai-recommend-status";
      status.append(createEl("span", "", message));
      if (settingsUrl) {
        const link = createEl("a", "button quiet small", "去设置 → API 配置");
        link.href = settingsUrl;
        if (typeof opts.onOpenSettings === "function") {
          link.addEventListener("click", (event) => {
            event.preventDefault();
            opts.onOpenSettings(settingsUrl);
          });
        }
        status.append(link);
      }
    };

    const showOverwritePreview = (result) => {
      pendingDraft = result;
      previewBox.replaceChildren();
      previewBox.hidden = false;
      const head = createEl("div", "ai-recommend-preview-head");
      head.append(createEl("strong", "", `✨ AI 推荐结果${result.engine ? `（${result.engine}）` : ""}`));
      const actions = createEl("div", "ai-recommend-preview-actions");
      const applyBtn = createEl("button", "button primary small", "填入并覆盖");
      applyBtn.type = "button";
      applyBtn.addEventListener("click", () => {
        if (!pendingDraft) return;
        opts.applyResult?.(pendingDraft, { overwrite: true });
        opts.markAiDraft?.(true);
        clearFeedback();
        opts.onToast?.("已填入 AI 推荐答案（请核对后保存）", "success");
      });
      const dismissBtn = createEl("button", "button quiet small", "忽略");
      dismissBtn.type = "button";
      dismissBtn.addEventListener("click", clearFeedback);
      actions.append(applyBtn, dismissBtn);
      head.append(actions);

      const body = createEl("div", "ai-recommend-preview-body");
      if (opts.QB?.renderTypeset) {
        const ansLine = createEl("div", "ai-recommend-preview-row");
        ansLine.append(createEl("strong", "", "答案："));
        const ansContent = createEl("span");
        opts.QB.renderTypeset(ansContent, result.answer || "");
        ansLine.append(ansContent);
        const anaLine = createEl("div", "ai-recommend-preview-row");
        anaLine.append(createEl("strong", "", "解析："));
        const anaContent = createEl("div");
        opts.QB.renderTypeset(anaContent, result.analysis || "");
        anaLine.append(anaContent);
        body.append(ansLine, anaLine);
      } else {
        body.append(
          createEl("p", "", `答案：${result.answer || "（空）"}`),
          createEl("p", "", `解析：${result.analysis || "（空）"}`)
        );
      }
      previewBox.append(head, body);
    };

    const run = async () => {
      if (busy) return;
      const endpoint = typeof opts.getEndpoint === "function" ? opts.getEndpoint() : opts.endpoint;
      if (!endpoint) return;
      clearFeedback();
      busy = true;
      button.disabled = true;
      const previousLabel = button.textContent;
      button.textContent = "AI 正在推导…";
      controller?.abort();
      controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 90000);
      try {
        const payload = opts.getPayload ? opts.getPayload() : {};
        const response = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-QB-Request": "1" },
          body: JSON.stringify(payload),
          signal: controller.signal
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          if (response.status === 409 && data.code === "api_not_configured") {
            showNotice(data.error || "尚未配置答题 API，请先前往配置。");
            return;
          }
          throw new Error(data.error || `推荐失败（HTTP ${response.status}）`);
        }
        if (opts.hasExistingContent?.()) {
          showOverwritePreview(data);
        } else {
          opts.applyResult?.(data, { overwrite: false });
          opts.markAiDraft?.(true);
          opts.onToast?.("已生成 AI 推荐答案，请核对后保存", "success");
        }
      } catch (error) {
        if (error?.name === "AbortError") return;
        showNotice(error?.message || "AI 推荐答案失败，请重试", { settingsUrl: null });
      } finally {
        clearTimeout(timeout);
        busy = false;
        button.disabled = false;
        button.textContent = previousLabel;
      }
    };

    button.addEventListener("click", () => { void run(); });

    return {
      button,
      status,
      previewBox,
      clearFeedback,
      abort: () => controller?.abort()
    };
  }

  return Object.freeze({
    OPTION_KEYS,
    QBTableText,
    insertStemBlank,
    fitAutoHeight,
    buildEditorBar,
    buildStemToolbar,
    buildDynamicOptionEditor,
    bindPreviewCursorSync,
    createAiRecommendWidget
  });
});
