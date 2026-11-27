/*
 * 正式题库统一全屏三区「编辑」工作台（供 Eagle 题库与支持题面修改的题库入口复用）。
 *
 * 与录题界面共用 QBEditorHelpers + question-editor.css，实现：
 *   - 精简三分栏顶栏（← 返回 + 状态徽标 / ‹ 上一题 第N/M题 下一题 › / 题型 + 保存）
 *   - 左上：原卷截图（点击放大核验）
 *   - 左下：题干（含填空横线与表格工具）、动态 A–H 选项、题图槽位管理、
 *           默认展开的「答案与解析」（含 AI 推荐答案、传图/裁图/贴图、搁置旧解析、历史版本）
 *   - 右侧：整题实时排版预览与光标/公式联动
 *   - 原子分流保存：按实际改动仅发 /question、仅发 /solution，或先发 /question 迁移 ID 再发 /solution
 */
((root) => {
  "use strict";

  const TYPES = {
    single_choice: "单选题",
    multiple_choice: "多选题",
    fill_blank: "填空题",
    true_false: "判断题",
    free_response: "解答题",
    unknown: "题型未定"
  };

  /**
   * 创建统一题目与答案编辑工作台实例
   * @param {object} deps 宿主依赖（node, QB, S, W, api, post, formatStamp, notify, confirm, onSaved）
   */
  function create(deps) {
    const { node, QB, S, W, api, post, formatStamp, notify, confirm, onSaved } = deps;
    const H = root.QBEditorHelpers || (typeof require === "function" ? require("./qb-editor-helpers.js") : null);
    const OPTION_KEYS = H?.OPTION_KEYS || QB?.OPTION_KEYS || ["A", "B", "C", "D", "E", "F", "G", "H"];

    let dialog = null;
    let controls = null;
    let cursorSync = null;
    let aiWidget = null;
    let stemToolbar = null;
    let optionEditor = null;

    let items = [];
    let currentIndex = 0;
    let record = null;
    let openOptions = {};
    let returnFocus = null;

    let token = 0;
    let epoch = 0;
    let loading = false;
    let saving = false;
    let uploading = false;
    let closing = false;
    let previewTimer = 0;
    const pendingSaves = new Set();

    let cropStart = null;
    let cropRange = null;
    let cropZoom = 1;

    const itemLabel = (item) => `第 ${item?.exam_number ?? item?.number ?? "?"} 题`;

    function ensureHelpers() {
      if (!H) throw new Error("编辑器辅助模块 QBEditorHelpers 未加载");
    }

    function afterPaint(callback) {
      if (root.requestAnimationFrame) {
        root.requestAnimationFrame(() => root.requestAnimationFrame(() => root.setTimeout(callback, 0)));
      } else {
        root.setTimeout(callback, 0);
      }
    }

    function collectQuestionDraft() {
      if (!controls || !record) return null;
      const bodyMode = controls.modeSelect ? controls.modeSelect.value : record.bodyMode;
      const isSourceImage = bodyMode === "source_image";
      const qType = controls.typeSelect.value;
      const options = isSourceImage ? {} : (optionEditor?.collectValues() || {});
      const figures = isSourceImage
        ? []
        : (record.questionFigures || [])
          .filter((item) => !item.removed)
          .map((item) => ({ file: item.file, slot: item.slot }));
      return {
        body_mode: bodyMode,
        question_type: qType,
        stem: isSourceImage ? String(record.originalStem || "") : String(controls.stemInput.value || ""),
        options,
        figures
      };
    }

    function questionChanged() {
      if (!record || !record.questionEditable) return false;
      const draft = collectQuestionDraft();
      return Boolean(draft) && JSON.stringify(draft) !== record.questionBaseline;
    }

    function solutionChanged() {
      if (!record) return false;
      return S.signature(record.solutionValue) !== record.solutionBaseline;
    }

    function isAiUntouched() {
      if (!record || !(record.aiFields || []).length) return false;
      return String(record.initialAi?.answer || "") === String(record.solutionValue?.answer || "")
        && String(record.initialAi?.analysis || "") === String(record.solutionValue?.analysis || "");
    }

    function isDirty() {
      if (!record) return false;
      return questionChanged() || solutionChanged();
    }

    function syncTopBarState() {
      if (!controls || !record) return;
      const dirty = isDirty();
      const aiUntouched = isAiUntouched();
      const locked = !record.questionEditable;

      if (dirty) {
        controls.barObj.setBadge("● 未保存改动", "dirty");
      } else if (aiUntouched) {
        controls.barObj.setBadge("AI 初稿 · 待核对", "ai");
      } else if (locked) {
        controls.barObj.setBadge("仅可改答案", "locked");
      } else {
        controls.barObj.setBadge("", "neutral");
      }

      controls.barObj.countEl.textContent = items.length > 1
        ? `第 ${currentIndex + 1} / ${items.length} 题`
        : itemLabel(record.item);
      controls.barObj.prevButton.disabled = loading || saving || uploading || currentIndex <= 0;
      controls.barObj.nextButton.disabled = loading || saving || uploading || currentIndex >= items.length - 1;

      const adoptAi = aiUntouched && (!record.hasSavedSolution || dirty);
      controls.barObj.saveButton.textContent = adoptAi ? "采纳并保存" : "保存";
      controls.barObj.saveButton.title = adoptAi
        ? "当前答案与解析为 AI 参考初稿；点击后保存并正式入库（Ctrl+Enter / Ctrl+S）"
        : "保存当前修改到题库，保留历史版本（Ctrl+Enter / Ctrl+S · Esc 返回）";
    }

    function syncAiFieldBadges() {
      if (!controls || !record) return;
      const fields = record.aiFields || [];
      const initialAi = record.initialAi || {};
      const stillAi = (field) => Boolean(fields.includes(field))
        && String(initialAi[field] || "").trim() !== ""
        && String(initialAi[field] || "") === String(record.solutionValue?.[field] || "");
      controls.answerWrap.classList?.toggle("is-ai-suggested", stillAi("answer"));
      controls.analysisWrap.classList?.toggle("is-ai-suggested", stillAi("analysis"));
    }

    function fitTextBoxes() {
      if (!controls || !H) return;
      const winH = typeof root.innerHeight === "number" && root.innerHeight > 0 ? root.innerHeight : 900;
      H.fitAutoHeight(controls.stemInput, { minRows: 3, maxHeightPx: Math.round(winH * 0.34) });
      H.fitAutoHeight(controls.answerInput, { minRows: 2, maxHeightPx: 180 });
      H.fitAutoHeight(controls.analysisInput, { minRows: 4, maxHeightPx: 320 });
    }

    function syncQuestionEditability() {
      if (!controls || !record) return;
      const busy = loading || saving || uploading;
      const isSourceImage = controls.modeSelect.value === "source_image";
      const canEditText = record.questionEditable && !isSourceImage;

      controls.typeSelect.disabled = busy || !record.questionEditable;
      controls.modeSelect.disabled = busy || !record.questionEditable;
      controls.stemInput.disabled = busy || !canEditText;
      stemToolbar?.setDisabled(busy || !canEditText);
      optionEditor?.setDisabled(busy || !canEditText);

      const unknownOpt = controls.unknownTypeOption;
      if (unknownOpt) {
        unknownOpt.hidden = !isSourceImage && controls.typeSelect.value !== "unknown";
      }

      if (controls.figureManager) {
        controls.figureManager.hidden = !(record.questionFigures || []).length || isSourceImage;
        for (const el of controls.figureManager.querySelectorAll?.("select, button") || []) {
          el.disabled = busy || !record.questionEditable;
        }
      }

      const activeFigs = (record.questionFigures || []).filter((fig) => !fig.removed);
      optionEditor?.syncVisibility(controls.typeSelect.value, activeFigs);

      controls.answerInput.disabled = busy;
      controls.analysisInput.disabled = busy;
      controls.uploadInput.disabled = busy;
      controls.cropToggle.disabled = busy || !record.item?.document_id;
      controls.restoreBtn.disabled = busy || !record.setAside;
      if (aiWidget?.button) aiWidget.button.disabled = busy;
      for (const row of controls.solutionFiguresList.children || []) {
        for (const el of row.querySelectorAll?.("input, select, button") || []) {
          el.disabled = busy || el.dataset?.unavailable === "1";
        }
      }
      controls.barObj.saveButton.disabled = busy || pendingSaves.has(record.id);
    }

    function updateStatusLine(customText = "", isError = false) {
      if (!controls || !record) return;
      controls.statusLine.classList?.toggle("is-error", Boolean(isError));
      if (customText) {
        controls.statusLine.textContent = customText;
        return;
      }
      const parts = [];
      parts.push(isDirty() ? "有未保存修改" : "已载入题库版本");
      if (record.solutionError) {
        parts.push(`解析读取异常：${record.solutionError}`);
      } else if (S.hasContent(record.solutionValue)) {
        if (!record.solutionValue.analysis.trim() && !record.solutionValue.figures.length) {
          parts.push("仅答案，建议补充解析");
        }
      } else {
        parts.push("尚未补充答案解析");
      }
      if ((record.aiFields || []).length) {
        parts.push("含 AI 初稿，请核对后保存");
      } else if (record.aiStale) {
        parts.push("旧 AI 初稿与当前题面不符，未填入");
      }
      if (pendingSaves.has(record.id)) {
        parts.push("上一笔保存仍在核对");
      }
      controls.statusLine.textContent = parts.join(" · ");
      controls.statusLine.title = record.aiStale
        ? (record.aiError || "现有 AI 参考答案对应的题面已变化，未自动填入。")
        : "保存题干/选项将生成新版并保留历史；保存解析不影响原卷与旧组卷。";
    }

    function renderPreview() {
      if (!controls || !record || !dialog?.open) return;
      const bodyMode = controls.modeSelect.value || record.bodyMode || "text";
      const activeFigs = (record.questionFigures || [])
        .filter((fig) => !fig.removed)
        .map((fig) => ({ ...fig.raw, slot: fig.slot }));
      const previewContent = {
        body_mode: bodyMode,
        question_images: record.questionImages || [],
        stem: bodyMode === "source_image" ? record.originalStem : controls.stemInput.value,
        options: bodyMode === "source_image" ? {} : (optionEditor?.collectValues() || {}),
        answer: record.solutionValue.answer,
        analysis: record.solutionValue.analysis,
        figures: activeFigs,
        sources: record.sources || []
      };
      const scroll = { top: controls.preview.scrollTop, left: controls.preview.scrollLeft };
      QB.renderQuestion(controls.preview, previewContent, {
        showNumber: false,
        showAnswer: "open",
        showEmptyAnswer: true,
        trackSource: true,
        solutionFigures: record.solutionValue.figures || []
      });
      QB.fitOptions?.(controls.preview);
      controls.preview.scrollTop = scroll.top;
      controls.preview.scrollLeft = scroll.left;
      cursorSync?.showPosition();
    }

    function schedulePreview() {
      root.clearTimeout(previewTimer);
      previewTimer = root.setTimeout(() => {
        if (dialog?.open && record) renderPreview();
      }, 60);
    }

    function refreshAllSync() {
      if (!record) return;
      syncAiFieldBadges();
      syncTopBarState();
      syncQuestionEditability();
      updateStatusLine();
      renderPreview();
    }

    function buildInputsMap() {
      const map = new Map();
      if (!controls) return map;
      map.set(controls.stemInput, "stem");
      map.set(controls.answerInput, "answer");
      map.set(controls.analysisInput, "analysis");
      if (optionEditor?.inputs) {
        OPTION_KEYS.forEach((key) => {
          if (optionEditor.inputs[key]) map.set(optionEditor.inputs[key], key);
        });
      }
      return map;
    }

    function renderQuestionFigures() {
      if (!controls || !record) return;
      const box = controls.figureManager;
      box.replaceChildren();
      const list = record.questionFigures || [];
      if (!list.length) {
        box.hidden = true;
        return;
      }
      box.hidden = controls.modeSelect.value === "source_image";
      box.append(node("strong", "", "题目配图位置"));
      list.forEach((fig, index) => {
        const row = node("div", `editor-figure-row${fig.removed ? " is-removed" : ""}`);
        const img = node("img");
        img.src = fig.url || "";
        img.alt = `题目配图 ${index + 1}`;

        const select = node("select");
        select.setAttribute("aria-label", `题目配图 ${index + 1} 位置`);
        ["stem", ...OPTION_KEYS].forEach((slot) => {
          const opt = node("option", "", slot === "stem" ? "题干" : `选项 ${slot}`);
          opt.value = slot;
          select.append(opt);
        });
        select.value = fig.slot || "stem";
        select.addEventListener("change", () => {
          fig.slot = select.value;
          if (OPTION_KEYS.includes(fig.slot)) optionEditor?.ensureKeyVisible(fig.slot);
          optionEditor?.updateFigures((record.questionFigures || []).filter((item) => !item.removed));
          refreshAllSync();
        });

        const toggleBtn = node("button", "button small", fig.removed ? "恢复" : "移除");
        toggleBtn.type = "button";
        toggleBtn.addEventListener("click", () => {
          fig.removed = !fig.removed;
          toggleBtn.textContent = fig.removed ? "恢复" : "移除";
          row.classList?.toggle("is-removed", fig.removed);
          optionEditor?.updateFigures((record.questionFigures || []).filter((item) => !item.removed));
          refreshAllSync();
        });

        row.append(img, select, toggleBtn);
        box.append(row);
      });
    }

    function renderSolutionFigures() {
      if (!controls || !record) return;
      controls.solutionFiguresList.replaceChildren();
      record.figureInputs = [];
      (record.solutionValue.figures || []).forEach((figure, index) => {
        const row = node("div", "answer-image-row");
        const image = node("img");
        image.src = figure.url;
        image.alt = `解析配图 ${index + 1}`;
        const fields = node("div", "answer-image-fields");

        const widthLabel = node("label", "", "宽度（毫米）");
        const width = node("input");
        width.type = "number";
        width.min = "5";
        width.max = "178";
        width.step = ".01";
        width.value = figure.display_width;
        widthLabel.append(width);
        width.addEventListener("input", () => {
          const val = Number(width.value);
          if (String(width.value).trim() && Number.isFinite(val) && val >= 5 && val <= 178) {
            figure.display_width = Math.round(val * 100) / 100;
            schedulePreview();
          }
          syncTopBarState();
        });
        width.addEventListener("change", () => {
          figure.display_width = Math.round(Math.max(5, Math.min(178, Number(width.value) || 20)) * 100) / 100;
          width.value = figure.display_width;
          refreshAllSync();
        });

        const position = node("select");
        position.setAttribute("aria-label", `解析图 ${index + 1} 插入位置`);
        [["before", "解析前"], ["after", "解析后"], ["paragraph", "指定段落后"]].forEach(([value, label]) => {
          const opt = node("option", "", label);
          opt.value = value;
          position.append(opt);
        });
        position.value = figure.position;

        const paragraph = node("input");
        paragraph.type = "number";
        paragraph.min = "1";
        paragraph.max = "1001";
        paragraph.value = (figure.paragraph || 0) + 1;
        paragraph.setAttribute("aria-label", "插入到第几段后");
        paragraph.hidden = figure.position !== "paragraph";

        position.addEventListener("change", () => {
          figure.position = position.value;
          paragraph.hidden = figure.position !== "paragraph";
          refreshAllSync();
        });
        paragraph.addEventListener("input", () => {
          const val = Number(paragraph.value);
          if (String(paragraph.value).trim() && Number.isInteger(val) && val >= 1 && val <= 1001) {
            figure.paragraph = val - 1;
            schedulePreview();
          }
          syncTopBarState();
        });
        paragraph.addEventListener("change", () => {
          figure.paragraph = Math.max(0, Math.min(1000, Math.floor(Number(paragraph.value) || 1) - 1));
          paragraph.value = figure.paragraph + 1;
          refreshAllSync();
        });

        const actions = node("div", "answer-image-tools");
        const move = (direction) => {
          const list = record.solutionValue.figures;
          const to = index + direction;
          if (to < 0 || to >= list.length) return;
          [list[index], list[to]] = [list[to], list[index]];
          renderSolutionFigures();
          refreshAllSync();
        };
        for (const [label, direction] of [["上移", -1], ["下移", 1]]) {
          const btn = node("button", "button quiet small", label);
          btn.type = "button";
          btn.disabled = index + direction < 0 || index + direction >= record.solutionValue.figures.length;
          btn.dataset.unavailable = btn.disabled ? "1" : "0";
          btn.addEventListener("click", () => move(direction));
          actions.append(btn);
        }
        const remove = node("button", "button quiet small", "移除");
        remove.type = "button";
        remove.addEventListener("click", () => {
          record.solutionValue.figures.splice(index, 1);
          renderSolutionFigures();
          refreshAllSync();
        });
        actions.append(remove);

        fields.append(widthLabel, position, paragraph, actions);
        row.append(image, fields);
        controls.solutionFiguresList.append(row);
        record.figureInputs.push({ figure, width, paragraph });
      });
    }

    function renderVersions() {
      if (!controls || !record) return;
      const history = Array.isArray(record.history) ? record.history.filter((row) => row && row.id) : [];
      controls.versions.hidden = history.length < 2;
      controls.versionsSummary.textContent = history.length > 1 ? `这份解析有 ${history.length} 个版本` : "这份解析的版本";
      controls.versionsBody.replaceChildren();
      history.forEach((row) => {
        const isCurrent = Boolean(record.base) && row.id === record.base;
        const entry = node("div", `answer-version${isCurrent ? " current" : ""}`);
        const head = node("div", "answer-version-head");
        head.append(node("strong", "", isCurrent ? "当前在用" : "以前那一版"));
        head.append(node("span", "hint", formatStamp(row.created_at)));
        if (row.needs_check) head.append(node("span", "editor-bar-badge is-locked", "和现在的题面对不上"));
        entry.append(head);

        const body = node("div", "paper answer-version-body");
        body.hidden = true;
        S.render(body, row, { node, QB, empty: "这一版没有文字。" });
        entry.append(body);

        const actions = node("div", "answer-image-tools");
        const showBtn = node("button", "button small", "看内容");
        showBtn.type = "button";
        showBtn.addEventListener("click", () => {
          body.hidden = !body.hidden;
          showBtn.textContent = body.hidden ? "看内容" : "收起内容";
        });
        actions.append(showBtn);

        if (!isCurrent) {
          const backBtn = node("button", "button small", "填回编辑区");
          backBtn.type = "button";
          backBtn.addEventListener("click", async () => {
            if (loading || saving) return;
            const session = epoch;
            if (S.hasContent(record.solutionValue) && solutionChanged()
              && !(await confirm({
                title: "用这一版盖掉现在编辑区里的内容？",
                text: "你在本题已经改过答案或解析。填回后这里会换成这一版，刚才改的不会留着。",
                ok: "填回"
              }))) {
              return;
            }
            if (session !== epoch || !dialog?.open || !record) return;
            record.solutionValue = S.editable(row);
            record.aiFields = [];
            record.initialAi = {};
            controls.answerInput.value = record.solutionValue.answer;
            controls.analysisInput.value = record.solutionValue.analysis;
            fitTextBoxes();
            renderSolutionFigures();
            refreshAllSync();
            notify("这一版已填进编辑区。核对后点保存才会入库。");
          });
          actions.append(backBtn);
        }
        entry.append(actions);
        controls.versionsBody.append(entry);
      });
    }

    function clearCrop() {
      cropStart = null;
      cropRange = null;
      if (controls) {
        controls.cropBox.hidden = true;
        controls.cropSave.disabled = true;
      }
    }

    function updateCrop(point) {
      const clamped = point.map((val) => Math.max(0, Math.min(1000, val)));
      cropRange = [
        Math.min(cropStart[0], clamped[0]),
        Math.min(cropStart[1], clamped[1]),
        Math.max(cropStart[0], clamped[0]),
        Math.max(cropStart[1], clamped[1])
      ];
      const [x0, y0, x1, y1] = cropRange;
      Object.assign(controls.cropBox.style, {
        left: `${x0 / 10}%`,
        top: `${y0 / 10}%`,
        width: `${(x1 - x0) / 10}%`,
        height: `${(y1 - y0) / 10}%`
      });
      controls.cropBox.hidden = false;
    }

    function showCropPage() {
      if (!controls || !record?.item?.document_id) return;
      clearCrop();
      cropZoom = 1;
      controls.cropSurface.style.width = "100%";
      controls.cropImage.dataset.ready = "false";
      controls.cropImage.src = `/api/documents/${encodeURIComponent(record.item.document_id)}/pages/${Number(controls.pages.value)}/preview`;
    }

    async function openCrop() {
      if (!record?.item?.document_id || loading || saving || uploading) return;
      const requestToken = token;
      const targetRecord = record;
      controls.crop.hidden = false;
      controls.pages.replaceChildren();
      controls.cropImage.dataset.ready = "false";
      clearCrop();
      let show = true;
      try {
        show = root.localStorage?.getItem("qb-crop-guidance") !== "0";
      } catch {
        // 无法读取本地偏好时保持默认显示引导提示
      }
      controls.cropHint.hidden = !show;
      controls.hintToggle.checked = show;
      try {
        const body = await api(`/api/papers/${encodeURIComponent(targetRecord.item.document_id)}`);
        if (requestToken !== token || !dialog?.open || record !== targetRecord) return;
        for (const page of body.paper?.pages || body.pages || []) {
          const opt = node("option", "", `第 ${page.page_idx + 1} 页`);
          opt.value = page.page_idx;
          controls.pages.append(opt);
        }
        if (!controls.pages.children.length) throw new Error("原卷页暂时无法打开，请使用上传或粘贴图片");
        const first = (targetRecord.sources || []).find((src) => Number.isInteger(src.page_idx));
        if (first) controls.pages.value = first.page_idx;
        showCropPage();
      } catch (error) {
        if (requestToken === token && dialog?.open) notify(error.message, "error");
      }
    }

    async function saveCrop() {
      if (!record || !cropRange || cropStart || saving || uploading || loading) return;
      const targetRecord = record;
      const requestToken = token;
      const requestEpoch = epoch;
      const bbox = cropRange.map(Math.round);
      uploading = true;
      syncQuestionEditability();
      controls.cropSave.disabled = true;
      try {
        const body = await post(`/api/library/${encodeURIComponent(targetRecord.id)}/solution-images`, {
          page_idx: Number(controls.pages.value),
          bbox
        });
        if (requestToken !== token || !dialog?.open || record !== targetRecord) return;
        targetRecord.solutionValue.figures.push(...S.figuresOf({ figures: [body.figure] }));
        clearCrop();
        controls.crop.hidden = true;
        renderSolutionFigures();
        refreshAllSync();
      } catch (error) {
        if (requestToken === token && dialog?.open) {
          notify(error.message, "error");
          controls.cropSave.disabled = false;
        }
      } finally {
        if (requestEpoch === epoch) {
          uploading = false;
          syncQuestionEditability();
        }
      }
    }

    async function uploadFiles(files) {
      if (!record || saving || uploading || loading || !files.length) return;
      const targetRecord = record;
      const requestToken = token;
      const requestEpoch = epoch;
      uploading = true;
      syncQuestionEditability();
      try {
        for (const file of files) {
          if (!/image\/(png|jpeg|webp)/.test(file.type)) throw new Error("请选择 PNG、JPEG 或 WebP 图片");
          if (file.size > 12 * 1024 * 1024) throw new Error("解析图较大，请压缩到 12 MB 以内再上传");
          const form = new FormData();
          form.append("image", file, file.name || "解析图.png");
          const body = await api(`/api/library/${encodeURIComponent(targetRecord.id)}/solution-images`, {
            method: "POST",
            headers: { "X-QB-Request": "1" },
            body: form
          });
          if (requestToken !== token || !dialog?.open || record !== targetRecord) return;
          targetRecord.solutionValue.figures.push(...S.figuresOf({ figures: [body.figure] }));
          renderSolutionFigures();
          refreshAllSync();
        }
      } catch (error) {
        if (requestEpoch === epoch && dialog?.open) notify(error.message, "error");
      } finally {
        if (requestEpoch === epoch) {
          uploading = false;
          syncQuestionEditability();
        }
      }
    }

    function closeSourceLightbox() {
      if (!controls?.sourceLightbox || controls.sourceLightbox.hidden) return false;
      controls.sourceLightbox.hidden = true;
      controls.sourceLightboxImg?.removeAttribute?.("src");
      return true;
    }

    async function loadSourceLightboxPages(docId, preferredPageIdx) {
      if (!controls?.sourceLightbox || !docId) return;
      const requestToken = token;
      try {
        const body = await api(`/api/papers/${encodeURIComponent(docId)}`);
        if (requestToken !== token || !dialog?.open || controls.sourceLightbox.hidden) return;
        const pageList = body.paper?.pages || body.pages || [];
        if (!pageList.length) return;
        controls.sourceLightboxPages.replaceChildren();
        pageList.forEach((page) => {
          const opt = node("option", "", `第 ${page.page_idx + 1} 页`);
          opt.value = page.page_idx;
          controls.sourceLightboxPages.append(opt);
        });
        const target = pageList.some((p) => p.page_idx === preferredPageIdx)
          ? preferredPageIdx
          : pageList[0].page_idx;
        controls.sourceLightboxPages.value = target;
        controls.sourceLightboxPages.hidden = false;
      } catch {
        // 即使整卷列表没取到，当前裁片高清放大图依然可以正常看
      }
    }

    function openSourceViewer() {
      if (!record?.item) return;
      if (typeof openOptions.onOpenSource === "function") {
        openOptions.onOpenSource(record.item);
        return;
      }
      if (!controls?.sourceLightbox) return;
      const box = controls.sourceLightbox;
      const img = controls.sourceLightboxImg;
      const docId = record.item.document_id || "";
      const firstPage = (record.sources || []).find((src) => Number.isInteger(src.page_idx))?.page_idx ?? 0;

      box.dataset.zoom = "100";
      controls.sourceLightboxTitle.textContent = `${itemLabel(record.item)} · 原卷对照`;
      controls.sourceLightboxZoomLabel.textContent = "100%";
      controls.sourceLightboxModeBtn.hidden = !docId;
      controls.sourceLightboxModeBtn.textContent = "看整页原卷";
      controls.sourceLightboxPages.hidden = true;
      controls.sourceLightboxPages.replaceChildren();
      box.dataset.viewMode = "crop";
      img.style.width = "min(960px, 92vw)";
      img.src = controls.sourceImg?.src || `/api/library/${encodeURIComponent(record.id)}/crop`;
      box.hidden = false;

      if (docId) void loadSourceLightboxPages(docId, firstPage);
    }

    function init() {
      if (dialog) return;
      ensureHelpers();

      dialog = node("dialog", "qb-editor-shell no-source");
      dialog.id = "unifiedEditorDialog";
      dialog.setAttribute("aria-label", "题目与答案统一编辑工作台");

      const typeSelect = node("select");
      typeSelect.setAttribute("aria-label", "题型");
      let unknownTypeOption = null;
      Object.entries(TYPES).forEach(([key, label]) => {
        const option = node("option", "", label);
        option.value = key;
        if (key === "unknown") unknownTypeOption = option;
        typeSelect.append(option);
      });

      const barObj = H.buildEditorBar({
        createEl: node,
        backLabel: "← 返回",
        backTitle: "返回题库列表；有未保存修改会先确认（Esc）",
        onBack: () => { void close(); },
        countText: "",
        onPrev: () => { void stepQuestion(-1); },
        onNext: () => { void stepQuestion(1); },
        prevDisabled: true,
        nextDisabled: true,
        typeSelect,
        saveLabel: "保存",
        saveTitle: "Ctrl+Enter / Ctrl+S 保存 · Esc 返回",
        onSave: () => { void saveCurrent(); }
      });

      const sourcePane = node("div", "card-source");
      const sourceSticky = node("div", "source-sticky");
      const sourceHead = node("div", "source-head");
      sourceHead.style.display = "flex";
      sourceHead.style.alignItems = "center";
      sourceHead.style.justifyContent = "space-between";
      sourceHead.style.gap = "8px";
      const sourceLabel = node("p", "source-label", "🔍 原卷截图 · 点击放大对照");
      const sourceRecropBtn = node("button", "button quiet small", "✂️ 重框/重识");
      sourceRecropBtn.type = "button";
      sourceRecropBtn.title = "如果原图少裁了一部分，直接打开原卷画新框并重新识读";
      sourceRecropBtn.addEventListener("click", () => {
        if (!record?.id || typeof root.openOriginalRecropModal !== "function") return;
        const targetId = String(record.id);
        void close().then(() => {
          root.openOriginalRecropModal(targetId);
        });
      });
      sourceHead.append(sourceLabel, sourceRecropBtn);
      const sourceCropFrame = node("div", "source-crop-frame");
      const sourceImg = node("img");
      sourceImg.alt = "题目原卷截图";
      sourceImg.title = "点击查看原卷高清扫描件";
      sourceImg.addEventListener("load", () => {
        if (dialog?.open && sourceImg.naturalWidth > 0) {
          dialog.classList?.remove("no-source");
        }
      });
      sourceImg.addEventListener("error", () => {
        dialog?.classList?.add("no-source");
      });
      sourceImg.addEventListener("click", openSourceViewer);
      sourceCropFrame.append(sourceImg);
      sourceSticky.append(sourceHead, sourceCropFrame);
      sourcePane.append(sourceSticky);

      const cardBody = node("div", "card-body");
      const form = node("form", "editor");
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        void saveCurrent();
      });

      const statusLine = node("p", "editor-status-line");
      statusLine.setAttribute("role", "status");
      statusLine.setAttribute("aria-live", "polite");

      const lockBanner = node("div", "editor-lock-banner");
      lockBanner.hidden = true;

      const modeSelect = node("select");
      modeSelect.setAttribute("aria-label", "正文形式");
      [["text", "文字题面"], ["source_image", "保留原图题面"]].forEach(([val, text]) => {
        const opt = node("option", "", text);
        opt.value = val;
        modeSelect.append(opt);
      });
      const modeField = node("label", "field inline");
      modeField.append(node("span", "", "正文"), modeSelect);
      modeField.hidden = true;

      const stemInput = node("textarea", "stem-input");
      stemInput.rows = 3;
      stemInput.maxLength = 20000;
      stemInput.spellcheck = false;
      stemInput.setAttribute("aria-label", "题干");
      const stemField = node("label", "field");
      stemField.append(node("span", "", "题干（公式用 $…$ 包住的 LaTeX）"), stemInput);

      stemToolbar = H.buildStemToolbar({
        stemInput,
        typeSelect,
        onToast: notify,
        createEl: node,
        afterChange: () => {
          fitTextBoxes();
          refreshAllSync();
        }
      });

      optionEditor = H.buildDynamicOptionEditor({
        optionKeys: OPTION_KEYS,
        maxLength: 4000,
        createEl: node,
        onInput: () => refreshAllSync(),
        onAdd: (_key, input) => {
          cursorSync?.attachInput(input);
          refreshAllSync();
        },
        onRemove: () => refreshAllSync()
      });

      const figureManager = node("div", "editor-figure-manager");
      figureManager.hidden = true;

      const answerSection = node("details", "extra editor-answer-section");
      answerSection.open = true;
      answerSection.append(node("summary", "", "答案与解析"));

      const answerHead = node("div", "editor-answer-head");
      answerHead.append(node("span", "editor-answer-subtitle", "支持 $…$ 公式；填空题多空按顺序写在同一答案框内"));

      const answerInput = node("textarea", "editor-answer-input");
      answerInput.rows = 2;
      answerInput.maxLength = 8000;
      answerInput.spellcheck = false;
      answerInput.placeholder = "如 B，或 x = 3";
      answerInput.setAttribute("aria-label", "答案");
      const answerWrap = node("div", "editor-answer-field-wrap");
      answerWrap.append(answerInput);
      const answerField = node("label", "field");
      answerField.append(node("span", "", "答案"), answerWrap);

      const analysisInput = node("textarea", "editor-analysis-input");
      analysisInput.rows = 4;
      analysisInput.maxLength = 100000;
      analysisInput.spellcheck = false;
      analysisInput.placeholder = "写出解题步骤。公式可写 $\\frac{1}{2}$；空一行分段。";
      analysisInput.setAttribute("aria-label", "解析");
      const analysisWrap = node("div", "editor-answer-field-wrap");
      analysisWrap.append(analysisInput);
      const analysisField = node("label", "field");
      analysisField.append(node("span", "", "解析"), analysisWrap);

      aiWidget = H.createAiRecommendWidget({
        QB,
        createEl: node,
        onToast: notify,
        getEndpoint: () => (record ? `/api/library/${encodeURIComponent(record.id)}/recommend-answer` : ""),
        getPayload: () => {
          const draft = collectQuestionDraft();
          return {
            stem: draft?.stem || "",
            options: draft?.options || {},
            question_type: draft?.question_type || "free_response",
            body_mode: draft?.body_mode || "text",
            figures: (draft?.figures || []).map((f) => ({ file: f.file, slot: f.slot }))
          };
        },
        hasExistingContent: () => Boolean(answerInput.value.trim() || analysisInput.value.trim()),
        applyResult: (result) => {
          if (!record) return;
          const nextAnswer = String(result.answer ?? "");
          const nextAnalysis = String(result.analysis ?? "");
          record.solutionValue.answer = nextAnswer;
          record.solutionValue.analysis = nextAnalysis;
          answerInput.value = nextAnswer;
          analysisInput.value = nextAnalysis;
          record.initialAi = { answer: nextAnswer, analysis: nextAnalysis };
          record.aiFields = [
            ...(nextAnswer.trim() ? ["answer"] : []),
            ...(nextAnalysis.trim() ? ["analysis"] : [])
          ];
          fitTextBoxes();
          refreshAllSync();
        },
        markAiDraft: () => {
          refreshAllSync();
        },
        onOpenSettings: async (url) => {
          if (await confirmLeaveIfDirty()) {
            forceClose();
            if (typeof openOptions.onOpenSettings === "function") openOptions.onOpenSettings(url);
            else root.location?.assign?.(url);
          }
        }
      });
      answerHead.append(aiWidget.button);

      const imageTools = node("div", "answer-image-tools");
      const uploadLabel = node("label", "button quiet small", "上传图");
      uploadLabel.title = "也可直接 Ctrl+V 粘贴剪贴板截图作为解析图";
      const uploadInput = node("input");
      uploadInput.type = "file";
      uploadInput.accept = "image/png,image/jpeg,image/webp";
      uploadInput.multiple = true;
      uploadInput.hidden = true;
      uploadInput.addEventListener("change", async () => {
        await uploadFiles(Array.from(uploadInput.files || []));
        uploadInput.value = "";
      });
      uploadLabel.append(uploadInput);

      const cropToggle = node("button", "button quiet small", "原卷裁图");
      cropToggle.type = "button";
      cropToggle.title = "从原卷框选解析配图；也可直接粘贴图片";
      cropToggle.addEventListener("click", () => { void openCrop(); });
      imageTools.append(uploadLabel, cropToggle);

      const solutionFiguresList = node("div", "answer-image-list");

      const crop = node("section", "answer-crop");
      crop.hidden = true;
      const cropBar = node("div", "answer-image-tools");
      const pages = node("select");
      pages.setAttribute("aria-label", "解析图来源页");
      pages.addEventListener("change", showCropPage);
      const cropSave = node("button", "button primary small", "加入解析图");
      cropSave.type = "button";
      cropSave.disabled = true;
      cropSave.addEventListener("click", () => { void saveCrop(); });
      const cropClose = node("button", "button quiet small", W?.CROP?.label || "收起裁图");
      cropClose.type = "button";
      cropClose.title = W?.CROP?.hint || "收起原卷裁图面板";
      cropClose.addEventListener("click", () => {
        crop.hidden = true;
        clearCrop();
      });
      const hintLabel = node("label", "answer-crop-hint-switch");
      const hintToggle = node("input");
      hintToggle.type = "checkbox";
      hintLabel.append(hintToggle, root.document.createTextNode("显示画框提示"));
      cropBar.append(pages, cropSave, cropClose, hintLabel);

      const cropHint = node("div", "answer-crop-guidance");
      cropHint.append(node("span", "", "点第一角 → 移动鼠标 → 点第二角固定。Esc 取消，Ctrl＋滚轮缩放。"));
      const hideHint = node("button", "button quiet small", "以后不再提示");
      hideHint.type = "button";
      const setHint = (show) => {
        cropHint.hidden = !show;
        hintToggle.checked = show;
        try {
          root.localStorage?.setItem("qb-crop-guidance", show ? "1" : "0");
        } catch {
          // 忽略受限环境下的本地存储错误
        }
      };
      hideHint.addEventListener("click", (event) => {
        event.stopPropagation();
        setHint(false);
      });
      hintToggle.addEventListener("change", () => setHint(hintToggle.checked));
      cropHint.append(hideHint);

      const cropScroll = node("div", "answer-crop-scroll");
      const cropSurface = node("div", "answer-crop-surface");
      const cropImage = node("img");
      cropImage.alt = "原卷解析图来源页";
      cropImage.draggable = false;
      cropImage.addEventListener("load", () => {
        if (dialog?.open && !crop.hidden) cropImage.dataset.ready = "true";
      });
      cropImage.addEventListener("error", () => {
        if (!dialog?.open || crop.hidden) return;
        cropImage.dataset.ready = "false";
        clearCrop();
        notify("这页原卷暂时无法显示，请换页重试，或上传、粘贴解析图。", "error");
      });
      const cropBox = node("div", "answer-crop-box");
      cropBox.hidden = true;
      cropSurface.append(cropImage, cropBox);
      cropScroll.append(cropSurface, cropHint);
      crop.append(cropBar, cropScroll);

      cropSurface.addEventListener("click", (event) => {
        if (cropImage.dataset.ready !== "true" || !cropImage.naturalWidth || uploading || event.button !== 0) return;
        const rect = cropImage.getBoundingClientRect();
        const point = [
          Math.max(0, Math.min(1000, ((event.clientX - rect.left) * 1000) / rect.width)),
          Math.max(0, Math.min(1000, ((event.clientY - rect.top) * 1000) / rect.height))
        ];
        if (!cropStart) {
          cropStart = point;
          cropRange = null;
          cropSave.disabled = true;
          updateCrop(point);
        } else {
          updateCrop(point);
          cropStart = null;
          cropSave.disabled = !cropRange || cropRange[2] - cropRange[0] < 2 || cropRange[3] - cropRange[1] < 2;
        }
      });
      cropSurface.addEventListener("pointermove", (event) => {
        if (!cropStart) return;
        const rect = cropImage.getBoundingClientRect();
        updateCrop([
          ((event.clientX - rect.left) * 1000) / rect.width,
          ((event.clientY - rect.top) * 1000) / rect.height
        ]);
      });
      cropScroll.addEventListener("wheel", (event) => {
        if (!event.ctrlKey) return;
        event.preventDefault();
        cropZoom = Math.max(0.5, Math.min(4, cropZoom * Math.exp(-event.deltaY * 0.002)));
        cropSurface.style.width = `${cropZoom * 100}%`;
      }, { passive: false });

      const setAside = node("section", "answer-set-aside");
      setAside.hidden = true;
      const setAsideTitle = node("strong", "", "题面改过，原来保存的解析还留着");
      const setAsideBody = node("div", "paper");
      const restoreBtn = node("button", "button small", "填回编辑区");
      restoreBtn.type = "button";
      restoreBtn.addEventListener("click", async () => {
        if (!record?.setAside || loading || saving) return;
        const session = epoch;
        if (S.hasContent(record.solutionValue) && solutionChanged()
          && !(await confirm({
            title: "用原来那份盖掉现在编辑区里的内容？",
            text: "你在本题已经改过答案或解析。填回后这里会换成原来那份，刚才改的不会留着。",
            ok: "填回"
          }))) {
          return;
        }
        if (session !== epoch || !dialog?.open || !record?.setAside) return;
        record.solutionValue = S.editable(record.setAside);
        record.aiFields = [];
        record.initialAi = {};
        answerInput.value = record.solutionValue.answer;
        analysisInput.value = record.solutionValue.analysis;
        fitTextBoxes();
        renderSolutionFigures();
        refreshAllSync();
        notify("原来那份解析已填进编辑区。核对后点保存才会入库。");
      });
      const asideTools = node("div", "answer-image-tools");
      asideTools.append(restoreBtn);
      setAside.append(
        setAsideTitle,
        node("p", "hint", "下面这份和当前题面对不上了。请核对后决定：填回、另写，或者保留当前内容。"),
        setAsideBody,
        asideTools
      );

      const versions = node("details", "answer-versions");
      const versionsSummary = node("summary", "", "这份解析的版本");
      const versionsBody = node("div", "answer-version-list");
      versions.append(versionsSummary, versionsBody);

      const originPanel = node("details", "answer-origin");
      originPanel.append(node("summary", "", "原卷解析"));
      const originBody = node("div", "paper");
      originPanel.addEventListener("toggle", () => {
        if (originPanel.open && record) {
          S.render(originBody, record.origin, { node, QB, empty: "原卷未提供答案解析。" });
        }
      });
      originPanel.append(originBody);

      answerSection.append(
        answerHead,
        aiWidget.status,
        aiWidget.previewBox,
        answerField,
        analysisField,
        imageTools,
        solutionFiguresList,
        crop,
        setAside,
        versions,
        originPanel
      );

      form.append(statusLine, lockBanner, modeField, stemField, stemToolbar.element, optionEditor.container, figureManager, answerSection);
      cardBody.append(form);

      const previewBox = node("div", "editor-preview-box");
      const positionHint = node("p", "editor-position-hint", "点击输入框，预览会标出正在修改的位置。");
      const preview = node("div", "editor-preview");
      previewBox.append(node("p", "preview-label", "预览 · 随输入实时更新"), positionHint, preview);

      const sourceLightbox = node("div", "editor-source-lightbox");
      sourceLightbox.hidden = true;
      const lbBar = node("div", "editor-source-lightbox-bar");
      const sourceLightboxTitle = node("strong", "", "原卷对照");
      const lbTools = node("div", "editor-source-lightbox-tools");
      const sourceLightboxPages = node("select");
      sourceLightboxPages.setAttribute("aria-label", "选择原卷页码");
      sourceLightboxPages.hidden = true;
      const sourceLightboxModeBtn = node("button", "button small", "看整页原卷");
      sourceLightboxModeBtn.type = "button";
      const zoomOutBtn = node("button", "button small", "－");
      zoomOutBtn.type = "button";
      zoomOutBtn.setAttribute("aria-label", "缩小原卷图");
      const sourceLightboxZoomLabel = node("span", "hint", "100%");
      const zoomInBtn = node("button", "button small", "＋");
      zoomInBtn.type = "button";
      zoomInBtn.setAttribute("aria-label", "放大原卷图");
      const lbCloseBtn = node("button", "button small primary", "关闭大图");
      lbCloseBtn.type = "button";
      lbTools.append(sourceLightboxPages, sourceLightboxModeBtn, zoomOutBtn, sourceLightboxZoomLabel, zoomInBtn, lbCloseBtn);
      lbBar.append(sourceLightboxTitle, lbTools);
      const lbStage = node("div", "editor-source-lightbox-stage");
      const sourceLightboxImg = node("img");
      sourceLightboxImg.alt = "原卷高清大图";
      lbStage.append(sourceLightboxImg);
      sourceLightbox.append(lbBar, lbStage);

      const applyLbZoom = (nextPct) => {
        const pct = Math.max(50, Math.min(300, Math.round(nextPct)));
        sourceLightbox.dataset.zoom = String(pct);
        sourceLightboxZoomLabel.textContent = `${pct}%`;
        sourceLightboxImg.style.width = `${Math.round((960 * pct) / 100)}px`;
      };
      zoomOutBtn.addEventListener("click", () => applyLbZoom((Number(sourceLightbox.dataset.zoom) || 100) - 25));
      zoomInBtn.addEventListener("click", () => applyLbZoom((Number(sourceLightbox.dataset.zoom) || 100) + 25));
      sourceLightboxPages.addEventListener("change", () => {
        if (!record?.item?.document_id) return;
        sourceLightbox.dataset.viewMode = "page";
        sourceLightboxModeBtn.textContent = "看本题裁片";
        sourceLightboxImg.src = `/api/documents/${encodeURIComponent(record.item.document_id)}/pages/${Number(sourceLightboxPages.value)}/preview`;
      });
      sourceLightboxModeBtn.addEventListener("click", () => {
        if (!record?.item?.document_id) return;
        if (sourceLightbox.dataset.viewMode === "page") {
          sourceLightbox.dataset.viewMode = "crop";
          sourceLightboxModeBtn.textContent = "看整页原卷";
          sourceLightboxImg.src = controls.sourceImg?.src || `/api/library/${encodeURIComponent(record.id)}/crop`;
        } else {
          sourceLightbox.dataset.viewMode = "page";
          sourceLightboxModeBtn.textContent = "看本题裁片";
          const pg = Number(sourceLightboxPages.value || 0);
          sourceLightboxImg.src = `/api/documents/${encodeURIComponent(record.item.document_id)}/pages/${pg}/preview`;
        }
      });
      lbCloseBtn.addEventListener("click", closeSourceLightbox);
      lbStage.addEventListener("click", (event) => {
        if (event.target === lbStage) closeSourceLightbox();
      });

      dialog.append(barObj.bar, sourcePane, cardBody, previewBox, sourceLightbox);
      root.document.body.append(dialog);

      controls = {
        barObj,
        typeSelect,
        unknownTypeOption,
        sourcePane,
        sourceImg,
        sourceLightbox,
        sourceLightboxTitle,
        sourceLightboxPages,
        sourceLightboxModeBtn,
        sourceLightboxZoomLabel,
        sourceLightboxImg,
        cardBody,
        form,
        statusLine,
        lockBanner,
        modeField,
        modeSelect,
        stemInput,
        figureManager,
        answerSection,
        answerWrap,
        answerInput,
        analysisWrap,
        analysisInput,
        uploadInput,
        cropToggle,
        solutionFiguresList,
        crop,
        pages,
        cropSave,
        cropImage,
        cropBox,
        cropSurface,
        cropScroll,
        cropHint,
        hintToggle,
        setAside,
        setAsideTitle,
        setAsideBody,
        restoreBtn,
        versions,
        versionsSummary,
        versionsBody,
        originPanel,
        originBody,
        preview,
        positionHint
      };

      cursorSync = H.bindPreviewCursorSync({
        R: QB,
        preview,
        positionHint,
        fieldLabel: (field) => ({ stem: "题干", answer: "答案", analysis: "解析" }[field] || `${field} 选项`),
        getInputsMap: buildInputsMap,
        initialInput: stemInput
      });

      const onQuestionInput = () => {
        fitTextBoxes();
        refreshAllSync();
      };
      stemInput.addEventListener("input", onQuestionInput);
      typeSelect.addEventListener("change", () => {
        stemToolbar.syncTypeState();
        refreshAllSync();
      });
      modeSelect.addEventListener("change", () => {
        refreshAllSync();
      });
      answerInput.addEventListener("input", () => {
        if (!record) return;
        record.solutionValue.answer = answerInput.value;
        fitTextBoxes();
        refreshAllSync();
      });
      analysisInput.addEventListener("input", () => {
        if (!record) return;
        record.solutionValue.analysis = analysisInput.value;
        fitTextBoxes();
        refreshAllSync();
      });

      root.addEventListener?.("resize", () => {
        if (dialog?.open) fitTextBoxes();
      });

      dialog.addEventListener("paste", (event) => {
        const files = Array.from(event.clipboardData?.items || [])
          .filter((item) => item.kind === "file" && item.type.startsWith("image/"))
          .map((item) => item.getAsFile())
          .filter(Boolean);
        if (files.length) {
          event.preventDefault();
          void uploadFiles(files);
        }
      });

      dialog.addEventListener("cancel", (event) => {
        event.preventDefault();
        if (closeSourceLightbox()) return;
        if (cropStart) {
          clearCrop();
          return;
        }
        void close();
      });

      dialog.addEventListener("keydown", (event) => {
        if (event.defaultPrevented || event.isComposing || event.keyCode === 229 || event.altKey) return;
        const lower = String(event.key || "").toLowerCase();
        if ((event.ctrlKey || event.metaKey) && !event.shiftKey && (lower === "s" || event.key === "Enter")) {
          event.preventDefault();
          if (!event.repeat) void saveCurrent();
        }
      });
    }

    function applyFocusField(focusField = "stem") {
      if (!controls || !record) return;
      const wantAnswer = focusField === "answer" || !record.questionEditable || controls.modeSelect.value === "source_image";
      if (wantAnswer) {
        controls.answerSection.open = true;
        const target = controls.answerInput;
        if (typeof controls.answerSection.scrollIntoView === "function") {
          controls.answerSection.scrollIntoView({ block: "nearest" });
        }
        target.focus?.({ preventScroll: true });
        cursorSync?.setActiveInput(target);
        cursorSync?.showPosition();
      } else {
        controls.stemInput.focus?.({ preventScroll: true });
        cursorSync?.setActiveInput(controls.stemInput);
        cursorSync?.showPosition();
      }
    }

    function applyRecordToControls(focusField = "stem", { focus = true } = {}) {
      if (!controls || !record) return;
      controls.typeSelect.value = record.questionType;
      controls.modeSelect.value = record.bodyMode;
      controls.modeField.hidden = !record.questionImages?.length;
      controls.stemInput.value = record.originalStem;

      const activeFigs = (record.questionFigures || []).filter((fig) => !fig.removed);
      optionEditor.setValues(record.originalOptions, activeFigs);
      buildInputsMap().forEach((_field, input) => cursorSync?.attachInput(input));

      controls.answerInput.value = record.solutionValue.answer;
      controls.analysisInput.value = record.solutionValue.analysis;

      controls.lockBanner.replaceChildren();
      if (!record.questionEditable) {
        controls.lockBanner.hidden = false;
        controls.lockBanner.append(
          node("strong", "", "当前仅可修改答案与解析："),
          node("span", "", record.questionLockReason || "题面暂不可直接修改")
        );
      } else if (record.bodyMode === "source_image") {
        controls.lockBanner.hidden = false;
        controls.lockBanner.append(
          node("strong", "", "当前为原图题面："),
          node("span", "", "如需编辑文字题干，可切换「正文：文字题面」或关闭后使用「AI 识别转文字」。")
        );
      } else {
        controls.lockBanner.hidden = true;
      }

      controls.setAsideBody.replaceChildren();
      controls.setAside.hidden = !record.setAside;
      if (record.setAside) {
        const from = record.setAside.from_version;
        controls.setAsideTitle.textContent = from
          ? `第 ${from} 版题面时填的解析，还在`
          : "题面改过，原来保存的解析还留着";
        S.render(controls.setAsideBody, record.setAside, { node, QB, empty: "原来那份解析没有文字。" });
      }

      controls.originBody.replaceChildren();
      if (controls.originPanel.open) {
        S.render(controls.originBody, record.origin, { node, QB, empty: "原卷未提供答案解析。" });
      }

      aiWidget?.clearFeedback();
      renderQuestionFigures();
      renderSolutionFigures();
      renderVersions();
      stemToolbar.syncTypeState();
      fitTextBoxes();
      refreshAllSync();

      dialog.classList?.add("no-source");
      controls.sourceImg.src = `/api/library/${encodeURIComponent(record.id)}/crop`;

      afterPaint(() => {
        if (!dialog?.open || !record) return;
        fitTextBoxes();
        if (focus) applyFocusField(focusField);
      });
    }

    async function loadQuestionAt(index, focusField = "stem") {
      if (saving || uploading) {
        notify("请等待当前保存或上传完成");
        return;
      }
      const item = items[index];
      if (!item) return;

      const requestToken = ++token;
      currentIndex = index;
      loading = true;
      record = null;
      controls.crop.hidden = true;
      clearCrop();
      aiWidget?.abort();
      aiWidget?.clearFeedback();

      controls.barObj.countEl.textContent = items.length > 1
        ? `第 ${index + 1} / ${items.length} 题`
        : itemLabel(item);
      controls.barObj.prevButton.disabled = true;
      controls.barObj.nextButton.disabled = true;
      controls.barObj.saveButton.disabled = true;
      controls.barObj.setBadge("载入中", "neutral");
      controls.statusLine.classList?.remove("is-error");
      controls.statusLine.textContent = `正在载入${itemLabel(item)}…`;
      controls.preview.replaceChildren();

      const id = String(item.id);
      let qBody = null;
      let qErr = "";
      let solBody = null;
      let solErr = "";

      const [qOutcome, solOutcome] = await Promise.allSettled([
        api(`/api/library/${encodeURIComponent(id)}/question`),
        api(`/api/library/${encodeURIComponent(id)}/solution`)
      ]);

      if (qOutcome.status === "fulfilled") qBody = qOutcome.value;
      else qErr = qOutcome.reason?.message || "题目题面读取失败";

      if (solOutcome.status === "fulfilled") solBody = solOutcome.value;
      else solErr = solOutcome.reason?.message || "答案解析读取失败";

      if (requestToken !== token || !dialog?.open) return;

      if (!qBody && !solBody) {
        loading = false;
        controls.statusLine.classList?.add("is-error");
        controls.statusLine.replaceChildren(node("span", "", qErr || solErr || "读取题目失败"));
        const retryBtn = node("button", "button small", "重试读取");
        retryBtn.type = "button";
        retryBtn.addEventListener("click", () => { void loadQuestionAt(index, focusField); });
        controls.statusLine.append(retryBtn);
        controls.barObj.prevButton.disabled = index <= 0;
        controls.barObj.nextButton.disabled = index >= items.length - 1;
        return;
      }

      const pub = qBody?.publication || {};
      const content = pub.content || item.content || {};
      const questionImages = Array.isArray(content.question_images) ? content.question_images : [];
      const rawFigures = Array.isArray(qBody?.figures) ? qBody.figures : (Array.isArray(content.figures) ? content.figures : []);
      const bodyMode = qBody?.body_mode || content.body_mode || "text";
      const qType = qBody?.question_type || pub.question_type || item.question_type || "free_response";
      const stem = String(qBody?.stem ?? content.stem ?? "");
      const options = (qBody?.options && typeof qBody.options === "object") ? qBody.options : (content.options || {});

      const questionFigures = rawFigures.map((fig) => ({
        file: fig.file,
        url: fig.url || "",
        slot: fig.slot || "stem",
        removed: false,
        raw: fig
      }));

      const questionEditable = qBody ? Boolean(qBody.editable) : false;
      const questionLockReason = qBody
        ? (qBody.editable ? "" : (qBody.reason || "当前题面暂不可直接修改"))
        : (qErr || "未能读取题面版本信息");

      const initialDraft = {
        body_mode: bodyMode,
        question_type: qType,
        stem: bodyMode === "source_image" ? stem : stem,
        options: bodyMode === "source_image" ? {} : Object.fromEntries(
          OPTION_KEYS.map((k) => [k, String(options[k] ?? "")]).filter(([, v]) => v.trim())
        ),
        figures: bodyMode === "source_image" ? [] : questionFigures.map((f) => ({ file: f.file, slot: f.slot }))
      };

      const solResponse = solBody || {};
      const solInit = solBody
        ? S.editorInitial(item, solResponse)
        : { value: S.editable(item.solution || item.content || {}), saved: S.signature(item.solution || item.content || {}), ai_fields: [], ai_stale: false };

      const hasSavedSolution = Boolean(
        solResponse.solution || (item.solution_revision !== "origin" && item.solution)
      );

      record = {
        id,
        item: { ...item, document_id: pub.document_id || item.document_id || "" },
        publication: pub,
        revision: qBody?.revision ?? null,
        contentHash: qBody?.content_hash || pub.content_hash || "",
        questionEditable,
        questionLockReason,
        bodyMode,
        questionType: qType,
        originalStem: stem,
        originalOptions: { ...options },
        questionImages,
        questionFigures,
        sources: Array.isArray(content.sources) ? content.sources : (Array.isArray(item.regions) ? item.regions : []),
        questionBaseline: JSON.stringify(initialDraft),
        solutionValue: solInit.value,
        solutionBaseline: solInit.saved,
        hasSavedSolution,
        base: solResponse.base_revision ?? null,
        origin: solResponse.origin || { answer: String(content.answer || ""), analysis: String(content.analysis || "") },
        history: Array.isArray(solResponse.history) ? solResponse.history : [],
        setAside: S.hasContent(solResponse.previous) ? solResponse.previous : null,
        aiFields: solInit.ai_fields || [],
        aiStale: Boolean(solInit.ai_stale),
        aiError: solInit.ai_error || "",
        initialAi: (solResponse.ai_answer_stale || !solResponse.ai_answer)
          ? {}
          : {
              answer: String(solResponse.ai_answer.answer || ""),
              analysis: String(solResponse.ai_answer.analysis || "")
            },
        solutionError: solErr,
        figureInputs: []
      };

      loading = false;
      applyRecordToControls(focusField);
    }

    async function confirmLeaveIfDirty() {
      if (!record) return true;
      const dirty = isDirty();
      if (!dirty && !saving) return true;
      return Boolean(await confirm({
        title: saving ? "返回并继续核对保存结果？" : "有未保存的修改",
        text: saving
          ? "保存请求已发出，返回不会撤销已接收的保存。收到结果后会提示是否成功。"
          : "当前对题面或答案解析的修改尚未保存，离开后不会写入题库。",
        ok: "放弃修改并离开",
        cancel: "继续编辑"
      }));
    }

    async function stepQuestion(delta) {
      if (!dialog?.open || loading || saving || uploading) return;
      const targetIndex = currentIndex + delta;
      if (targetIndex < 0 || targetIndex >= items.length) return;
      const session = epoch;
      if (!(await confirmLeaveIfDirty())) return;
      if (session !== epoch || !dialog?.open) return;
      await loadQuestionAt(targetIndex, openOptions.focusField || "stem");
    }

    async function saveCurrent() {
      if (!record || loading || saving || uploading || pendingSaves.has(record.id)) return;
      root.clearTimeout(previewTimer);

      const qChanged = questionChanged();
      const sChanged = solutionChanged();
      const adoptAiInitial = isAiUntouched() && !record.hasSavedSolution && S.hasContent(record.solutionValue);
      const needSaveSolution = sChanged || adoptAiInitial;

      if (!qChanged && !needSaveSolution) {
        notify("没有新的修改，已保持原题库内容。");
        return;
      }

      const qDraft = qChanged ? collectQuestionDraft() : null;
      if (qDraft) {
        if (qDraft.body_mode === "text" && !qDraft.stem.trim()) {
          notify("文字题干不能为空，请补充题干后再保存。", "error");
          controls.stemInput.focus();
          return;
        }
        if (qDraft.body_mode === "text" && qDraft.question_type === "unknown") {
          notify("文字题请选择具体题型（单选、多选、填空、判断或解答题）。", "error");
          controls.typeSelect.focus();
          return;
        }
      }

      if (needSaveSolution) {
        for (const { figure, width, paragraph } of record.figureInputs || []) {
          const size = Number(width.value);
          const at = Number(paragraph.value);
          if (!String(width.value).trim() || !Number.isFinite(size) || size < 5 || size > 178) {
            notify("请把解析图宽度填写为 5–178 毫米，再保存。", "error");
            width.focus();
            return;
          }
          if (figure.position === "paragraph" && (!String(paragraph.value).trim() || !Number.isInteger(at) || at < 1 || at > 1001)) {
            notify("请填写完整的解析段落序号，再保存。", "error");
            paragraph.focus();
            return;
          }
          figure.display_width = Math.round(size * 100) / 100;
          if (figure.position === "paragraph") figure.paragraph = at - 1;
        }
        if (!S.hasContent(record.solutionValue)) {
          notify("请填写答案、解析或添加解析图片后再保存。", "error");
          controls.answerInput.focus();
          return;
        }
      }

      const targetRecord = record;
      const previousId = targetRecord.id;
      const requestToken = token;
      const requestEpoch = epoch;
      let activeId = previousId;
      let latestPublication = targetRecord.publication || null;
      let nextBaseRevision = targetRecord.base;
      let savedSolution = targetRecord.item.solution || null;

      saving = true;
      pendingSaves.add(previousId);
      syncQuestionEditability();
      updateStatusLine("正在保存到题库…");

      try {
        if (qChanged && qDraft) {
          const qRes = await post(
            `/api/library/${encodeURIComponent(previousId)}/question`,
            {
              revision: targetRecord.revision,
              content_hash: targetRecord.contentHash,
              ...qDraft
            },
            { keepAfterClose: true }
          );
          if (qRes.publication) {
            latestPublication = qRes.publication;
            activeId = String(qRes.publication.id || previousId);
          }
          targetRecord.id = activeId;
          targetRecord.item.id = activeId;
          targetRecord.publication = latestPublication || targetRecord.publication;
          targetRecord.revision = qRes.revision ?? ((targetRecord.revision || 0) + 1);
          targetRecord.contentHash = qRes.content_hash || latestPublication?.content_hash || targetRecord.contentHash;
          targetRecord.bodyMode = qDraft.body_mode;
          targetRecord.questionType = qDraft.question_type;
          targetRecord.originalStem = latestPublication?.content?.stem ?? qDraft.stem;
          targetRecord.originalOptions = latestPublication?.content?.options ?? qDraft.options;
          const newPubFigs = latestPublication?.content?.figures;
          if (Array.isArray(newPubFigs)) {
            targetRecord.questionFigures = newPubFigs.map((fig) => ({
              file: fig.file,
              url: fig.url || "",
              slot: fig.slot || "stem",
              removed: false,
              raw: fig
            }));
          } else {
            targetRecord.questionFigures = (targetRecord.questionFigures || []).filter((fig) => !fig.removed);
          }
          targetRecord.questionBaseline = JSON.stringify({
            body_mode: targetRecord.bodyMode,
            question_type: targetRecord.questionType,
            stem: targetRecord.originalStem,
            options: targetRecord.bodyMode === "source_image"
              ? {}
              : Object.fromEntries(OPTION_KEYS.map((k) => [k, String(targetRecord.originalOptions[k] ?? "")]).filter(([, v]) => v.trim())),
            figures: targetRecord.bodyMode === "source_image"
              ? []
              : targetRecord.questionFigures.map((f) => ({ file: f.file, slot: f.slot }))
          });

          if (activeId !== previousId) {
            // 新版发布后旧版的 solution 不属于新版；清空 base_revision 让后端安全迁移新图
            nextBaseRevision = null;
            targetRecord.base = null;
            targetRecord.history = [];
            targetRecord.item.solution_needs_review = Boolean(latestPublication?.solution_needs_review);
          }

          const listItem = items.find((it) => String(it.id) === previousId);
          if (listItem) {
            listItem.id = activeId;
            listItem.question_type = targetRecord.questionType;
            listItem.content = latestPublication?.content || {
              ...(listItem.content || {}),
              stem: targetRecord.originalStem,
              options: targetRecord.originalOptions,
              body_mode: targetRecord.bodyMode,
              figures: targetRecord.questionFigures.map((f) => f.raw)
            };
            listItem.solution_needs_review = Boolean(latestPublication?.solution_needs_review);
          }
          targetRecord.item.question_type = targetRecord.questionType;
          targetRecord.item.content = latestPublication?.content || targetRecord.item.content;

          // 如果接下来不存解析，现在即刻同步给宿主列表（避免后续步骤若不触发则未通知宿主）
          if (!needSaveSolution) {
            onSaved?.(targetRecord.item, savedSolution, {
              sync: true,
              scope: openOptions.scope || "library",
              scopeContext: openOptions.scopeContext ?? null,
              publication: latestPublication,
              previousId,
              questionChanged: true,
              solutionChanged: false
            });
          }
        }

        if (needSaveSolution) {
          const solPayload = {
            ...S.payload(targetRecord.solutionValue),
            base_revision: nextBaseRevision ?? null,
            sync_library: true
          };
          const solRes = await post(
            `/api/library/${encodeURIComponent(activeId)}/solution`,
            solPayload,
            { keepAfterClose: true }
          );
          savedSolution = solRes.solution;
          targetRecord.item.solution = savedSolution;
          targetRecord.item.solution_revision = savedSolution.id;
          targetRecord.item.solution_needs_review = false;
          targetRecord.solutionValue = S.editable(savedSolution);
          targetRecord.solutionBaseline = S.signature(targetRecord.solutionValue);
          targetRecord.hasSavedSolution = true;
          targetRecord.aiFields = [];
          targetRecord.aiStale = false;
          targetRecord.initialAi = {};
          targetRecord.setAside = null;
          targetRecord.base = solRes.base_revision ?? savedSolution.id;
          targetRecord.history.unshift(savedSolution);

          onSaved?.(targetRecord.item, savedSolution, {
            sync: true,
            scope: openOptions.scope || "library",
            scopeContext: openOptions.scopeContext ?? null,
            publication: latestPublication,
            previousId,
            questionChanged: qChanged,
            solutionChanged: true
          });
        }

        if (requestToken !== token || !dialog?.open || record !== targetRecord) {
          notify(`已确认${itemLabel(targetRecord.item)}保存成功。`, "success");
          return;
        }
        applyRecordToControls(openOptions.focusField || "stem", { focus: false });
        const msg = qChanged && needSaveSolution
          ? "题目与答案解析均已保存到题库，旧版已保留"
          : qChanged
            ? "题目修改已保存到题库，旧版已保留"
            : "答案解析已保存到题库";
        updateStatusLine(msg);
        notify(msg, "success");
      } catch (error) {
        if (qChanged && activeId !== previousId) {
          // 题干已经先一步更新成功并迁出新 ID，先刷新宿主以免外部仍拿着旧 ID
          onSaved?.(targetRecord.item, savedSolution, {
            sync: true,
            scope: openOptions.scope || "library",
            scopeContext: openOptions.scopeContext ?? null,
            publication: latestPublication,
            previousId,
            questionChanged: true,
            solutionChanged: false
          });
        }
        const message = error?.message || "保存失败，请重试";
        if (requestToken === token && dialog?.open && record === targetRecord) {
          updateStatusLine(message, true);
        }
        notify(
          requestEpoch === epoch
            ? message
            : `${itemLabel(targetRecord.item)}保存未确认：${message}`,
          "error"
        );
      } finally {
        pendingSaves.delete(previousId);
        pendingSaves.delete(activeId);
        if (requestEpoch === epoch) saving = false;
        if (dialog?.open) {
          syncQuestionEditability();
          syncTopBarState();
        }
      }
    }

    async function open(supplied = [], options = {}) {
      init();
      ++token;
      ++epoch;
      root.clearTimeout(previewTimer);
      loading = false;
      saving = false;
      uploading = false;
      closing = false;
      openOptions = options || {};
      items = Array.isArray(supplied) ? supplied.map((it) => ({ ...it })) : [];
      if (!items.length) {
        notify("先选择一道题，再打开编辑工作台", "error");
        return;
      }
      const focusId = options.focus != null ? String(options.focus) : "";
      let startIdx = items.findIndex((it) => String(it.id) === focusId);
      if (startIdx < 0) startIdx = 0;

      returnFocus = root.document?.activeElement || null;
      if (!dialog.open) dialog.showModal?.();
      await loadQuestionAt(startIdx, options.focusField || "stem");
    }

    function forceClose() {
      if (!dialog?.open) return;
      closeSourceLightbox();
      ++token;
      const closedEpoch = ++epoch;
      root.clearTimeout(previewTimer);
      aiWidget?.abort();
      dialog.close?.();
      loading = false;
      saving = false;
      uploading = false;
      closing = false;
      cropStart = null;
      cropRange = null;
      const focus = returnFocus;
      afterPaint(() => {
        if (dialog?.open || closedEpoch !== epoch) return;
        clearCrop();
        if (controls) {
          controls.crop.hidden = true;
          controls.cropImage.removeAttribute?.("src");
          controls.sourceImg.removeAttribute?.("src");
          [controls.preview, controls.solutionFiguresList, controls.figureManager, controls.originBody, controls.versionsBody]
            .forEach((box) => box?.replaceChildren());
        }
        record = null;
        if (focus?.isConnected) focus.focus?.({ preventScroll: true });
      });
    }

    async function close() {
      if (closing || !dialog?.open) return;
      closing = true;
      const session = epoch;
      const confirmed = await confirmLeaveIfDirty();
      if (session !== epoch) return;
      if (!confirmed) {
        closing = false;
        return;
      }
      forceClose();
    }

    return Object.freeze({
      open,
      close,
      forceClose,
      isOpen: () => Boolean(dialog?.open),
      isDirty
    });
  }

  root.LibraryUnifiedEditor = Object.freeze({ create });
})(typeof window === "undefined" ? globalThis : window);
