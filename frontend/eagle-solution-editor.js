/* Eagle → LibraryUnifiedEditor（通过 LibraryAnswerEditor allowQuestionEdit）统一单题/列表桥接。
 *
 * 按需保障 /question-editor.css、/qb-editor-helpers.js、/library-solutions.js、
 * /control-wording.js、/library-unified-editor.js 与 /library-answer-editor.js 就绪，
 * 以当前筛选列表传入统一工作台，支持顶栏切题与题干/答案联合保存同步。
 */
(function () {
  "use strict";

  const ASSET_VER = "20261127a";
  const ASSETS = [
    {
      kind: "css",
      href: `/question-editor.css?v=${ASSET_VER}`,
      match: 'link[href^="/question-editor.css"]'
    },
    {
      kind: "js",
      src: `/qb-editor-helpers.js?v=${ASSET_VER}`,
      ready: () => Boolean(window.QBEditorHelpers)
    },
    {
      kind: "js",
      src: `/library-solutions.js?v=${ASSET_VER}`,
      ready: () => Boolean(window.LibrarySolutions)
    },
    {
      kind: "js",
      src: `/control-wording.js?v=${ASSET_VER}`,
      ready: () => Boolean(window.QBControlWording)
    },
    {
      kind: "js",
      src: `/library-unified-editor.js?v=${ASSET_VER}`,
      ready: () => Boolean(window.LibraryUnifiedEditor)
    },
    {
      kind: "js",
      src: `/library-answer-editor.js?v=${ASSET_VER}`,
      ready: () => Boolean(window.LibraryAnswerEditor)
    }
  ];

  let loadPromise = null;
  let editor = null;
  let opening = false;

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function notify(message, tone) {
    if (typeof window.showToast === "function") {
      const type = tone === "error" ? "error" : tone === "success" ? "success" : "info";
      window.showToast(String(message || ""), type);
      return;
    }
    if (message) console.info("[统一编辑]", message);
  }

  async function confirmDialog(options = {}) {
    const title = options.title || "确认";
    const text = options.text || "";
    const ok = options.ok || "确定";
    return window.confirm([title, text, `（确定 = ${ok}）`].filter(Boolean).join("\n\n"));
  }

  function loadAsset(asset) {
    if (asset.kind === "css") {
      if (asset.match && document.querySelector(asset.match)) return Promise.resolve();
      const existing = document.querySelector(`link[data-eagle-answer-asset="${asset.href}"]`);
      if (existing) return Promise.resolve();
      return new Promise((resolve, reject) => {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = asset.href;
        link.dataset.eagleAnswerAsset = asset.href;
        link.onload = () => resolve();
        link.onerror = () => reject(new Error(`样式加载失败：${asset.href}`));
        document.head.appendChild(link);
      });
    }
    if (typeof asset.ready === "function" && asset.ready()) return Promise.resolve();
    const existing = document.querySelector(`script[data-eagle-answer-asset="${asset.src}"]`);
    if (existing) {
      return existing.dataset.loaded === "1"
        ? Promise.resolve()
        : new Promise((resolve, reject) => {
            existing.addEventListener("load", () => resolve(), { once: true });
            existing.addEventListener("error", () => reject(new Error(`脚本加载失败：${asset.src}`)), { once: true });
          });
    }
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = asset.src;
      script.async = false;
      script.dataset.eagleAnswerAsset = asset.src;
      script.onload = () => { script.dataset.loaded = "1"; resolve(); };
      script.onerror = () => reject(new Error(`脚本加载失败：${asset.src}`));
      document.head.appendChild(script);
    });
  }

  function ensureAssets() {
    if (!loadPromise) {
      loadPromise = ASSETS.reduce(
        (chain, asset) => chain.then(() => loadAsset(asset)),
        Promise.resolve()
      ).catch((error) => {
        loadPromise = null;
        throw error;
      });
    }
    return loadPromise;
  }

  function hostList(name) {
    const value = window[name];
    return Array.isArray(value) ? value : [];
  }

  function findQuestion(id) {
    const wanted = String(id || "");
    if (!wanted) return null;
    const pool = hostList("questionsData");
    const filtered = hostList("filteredQuestions");
    return pool.find((q) => String(q.id) === wanted)
      || filtered.find((q) => String(q.id) === wanted)
      || null;
  }

  function resolveQuestionId(ids) {
    if (Array.isArray(ids) && ids.length) return String(ids[0]);
    if (ids != null && ids !== "" && !Array.isArray(ids)) return String(ids);
    if (window.currentActiveQuestionId) return String(window.currentActiveQuestionId);
    const filtered = hostList("filteredQuestions");
    if (filtered[0]?.id) return String(filtered[0].id);
    const all = hostList("questionsData");
    if (all[0]?.id) return String(all[0].id);
    return "";
  }

  async function fetchPublication(id) {
    const response = await fetch(`/api/library/${encodeURIComponent(id)}`, {
      headers: { "X-QB-Request": "1" },
      cache: "no-store"
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `读取题目失败（HTTP ${response.status}）`);
    return body.publication || body.item || body;
  }

  function toEditorItem(publication, fallback) {
    const id = String(publication?.id || fallback?.id || "");
    const number = publication?.exam_number
      ?? publication?.number
      ?? fallback?.questionNumber
      ?? fallback?.number
      ?? "?";
    const fallbackContent = fallback
      ? {
          stem: fallback.rawStem || "",
          options: fallback.rawOptions || {},
          body_mode: fallback.isSourceImage ? "source_image" : "text",
          question_images: fallback.questionImages || [],
          figures: fallback.figures || fallback.rawFigures || [],
          answer: fallback.rawAnswer || "",
          analysis: fallback.rawAnalysis || "",
          sources: fallback.regions || []
        }
      : {};
    const solution = publication?.solution ?? fallback?.savedSolution ?? null;
    return {
      id,
      exam_number: number,
      number,
      document_id: publication?.document_id || fallback?.documentId || "",
      question_type: publication?.question_type || fallback?.questionTypeCode || "free_response",
      content: publication?.content || fallbackContent,
      solution,
      solution_revision: solution?.id || null,
      solution_needs_review: Boolean(publication?.solution_needs_review ?? fallback?.solutionNeedsReview)
    };
  }

  function buildListForEditor(targetItem) {
    const filtered = hostList("filteredQuestions");
    const list = filtered.length ? filtered : hostList("questionsData");
    const mapped = list.map((q) => (
      String(q.id) === String(targetItem.id) ? targetItem : toEditorItem(null, q)
    ));
    if (!mapped.some((item) => String(item.id) === String(targetItem.id))) {
      mapped.unshift(targetItem);
    }
    return mapped;
  }

  function ensureEditor() {
    if (editor) return editor;
    const QB = window.QBRender || window.QB;
    if (!window.LibraryAnswerEditor?.create) throw new Error("编辑器核心脚本未就绪");
    if (!window.LibraryUnifiedEditor?.create) throw new Error("统一编辑工作台脚本未就绪");
    if (!window.LibrarySolutions) throw new Error("答案解析模型脚本未就绪");
    if (!QB?.renderQuestion) throw new Error("题面渲染脚本未就绪");
    editor = window.LibraryAnswerEditor.create({
      node,
      QB,
      notify,
      confirm: confirmDialog,
      onSaved: (item, solution, meta = {}) => {
        if (typeof window.applyUnifiedSaveToEagle === "function") {
          try {
            window.applyUnifiedSaveToEagle(item, solution, meta);
            return;
          } catch (error) {
            console.warn("统一编辑保存后同步 Eagle 视图失败", error);
          }
        }
        const card = findQuestion(meta.previousId || item.id);
        if (card) {
          if (solution) {
            card.savedSolution = solution;
            card.hasAnswer = Boolean(solution.answer || card.rawAnswer);
            card.hasAnalysis = Boolean(solution.analysis || card.rawAnalysis);
          }
          if (typeof window.refreshQuestionSolution === "function") {
            try { window.refreshQuestionSolution(card); } catch (error) {
              console.warn("保存后刷新题卡失败", error);
            }
          }
        }
        if (typeof window.renderQuestions === "function") {
          try { window.renderQuestions(); } catch (error) {
            console.warn("保存后重绘列表失败", error);
          }
        }
        if (typeof window.renderQuickLook === "function" && window.quickLookQuestionId
          && String(window.quickLookQuestionId) === String(item.id)) {
          try { window.renderQuickLook(); } catch (error) {
            console.warn("保存后刷新快速审题失败", error);
          }
        }
      }
    });
    return editor;
  }

  async function openSolutionEditor(ids, options = {}) {
    if (opening) return;
    const id = resolveQuestionId(ids);
    if (!id) {
      notify("先选一道题，再打开编辑工作台", "error");
      return;
    }
    opening = true;
    try {
      await ensureAssets();
      const publication = await fetchPublication(id);
      const item = toEditorItem(publication, findQuestion(id));
      const list = buildListForEditor(item);
      await ensureEditor().open(list, {
        scope: "library",
        allowQuestionEdit: true,
        focus: item.id,
        focusField: options.focusField || "stem",
        returnTo: options.returnTo || ""
      });
    } catch (error) {
      notify(error?.message || "打开统一编辑工作台失败", "error");
    } finally {
      opening = false;
    }
  }

  function closeSolutionEditor() {
    if (editor?.isOpen?.()) {
      void editor.close?.();
      return;
    }
    const unifiedDialog = document.getElementById("unifiedEditorDialog");
    if (unifiedDialog?.open) {
      const backBtn = unifiedDialog.querySelector(".editor-bar-back");
      if (backBtn) backBtn.click();
      else unifiedDialog.close?.();
      return;
    }
    const dialog = document.getElementById("answerEditorDialog");
    if (dialog?.open) {
      const closeBtn = dialog.querySelector(".answer-editor-bar .button-quiet, .answer-editor-bar button");
      if (closeBtn) closeBtn.click();
      else if (typeof dialog.close === "function") dialog.close();
    }
  }

  window.openSolutionEditor = openSolutionEditor;
  window.closeSolutionEditor = closeSolutionEditor;
})();
