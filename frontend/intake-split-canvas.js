/* 录题双栏分屏工作台与顶部三步进度条模块。
 *
 * 负责：
 *   1. 计算并渲染 3 步流程导航（① 导卷切题 → ② 逐题核对 → ③ 完成归档）
 *   2. 在桌面端右侧题卡流旁常驻展示左侧高清原卷画布（#intakeSplitPane）
 *   3. 维护「题卡 ↔ 原卷题框」双向定位联动：点卡自动翻页并高亮原卷框，点框滚动聚焦右侧题卡，双击直接进入局部调框
 */
(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.QBIntakeSplitCanvas = value;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const ACTIVE_STATUSES = new Set(["queued", "parsing", "segmenting", "preparing", "converting"]);

  /**
   * 校验并换算归一化坐标（0..1000）为百分比样式数值
   * @param {number[]} bbox [x0, y0, x1, y1]
   * @returns {{ left: number, top: number, width: number, height: number } | null}
   */
  function boxRectPercent(bbox) {
    if (!Array.isArray(bbox) || bbox.length !== 4) return null;
    const nums = bbox.map((v) => Number(v));
    if (nums.some((v) => !Number.isFinite(v))) return null;
    const x0 = Math.max(0, Math.min(1000, nums[0]));
    const y0 = Math.max(0, Math.min(1000, nums[1]));
    const x1 = Math.max(0, Math.min(1000, nums[2]));
    const y1 = Math.max(0, Math.min(1000, nums[3]));
    if (x1 <= x0 || y1 <= y0) return null;
    return {
      left: x0 / 10,
      top: y0 / 10,
      width: (x1 - x0) / 10,
      height: (y1 - y0) / 10,
    };
  }

  /**
   * 判定题目在原卷上的展示色调
   * @param {object} question 题目对象
   * @param {number|string|null} currentId 当前聚焦题目 ID
   * @returns {"active" | "approved" | "attention" | "pending"}
   */
  function questionBoxTone(question, currentId) {
    if (!question) return "pending";
    if (currentId != null && String(question.id) === String(currentId)) return "active";
    if (question.approved) return "approved";
    if (question.state === "reading_failed" || question.state === "needs_attention") return "attention";
    return "pending";
  }

  /**
   * 提取指定原卷页上的所有题目边框叠加数据
   * @param {object[]} questions 题目列表
   * @param {number} pageIdx 页码（0 基）
   * @param {number|string|null} currentId 当前聚焦题目 ID
   * @returns {Array<object>}
   */
  function pageBoxesForPage(questions, pageIdx, currentId = null) {
    if (!Array.isArray(questions) || !Number.isInteger(pageIdx)) return [];
    const items = [];
    for (const q of questions) {
      const regions = Array.isArray(q?.regions) ? q.regions : [];
      regions.forEach((region, regionIndex) => {
        if (region?.page_idx !== pageIdx) return;
        const rect = boxRectPercent(region.bbox);
        if (!rect) return;
        items.push({
          questionId: q.id,
          number: q.number,
          regionIndex,
          tone: questionBoxTone(q, currentId),
          approved: Boolean(q.approved),
          label: `第 ${q.number ?? "?"} 题`,
          rect,
        });
      });
    }
    return items;
  }

  /**
   * 获取题目的首个合法原卷页码
   * @param {object|null} question 题目对象
   * @param {Array<object>} pages 原卷页数组
   * @returns {number|null}
   */
  function findFirstPageForQuestion(question, pages = []) {
    const validPages = new Set((Array.isArray(pages) ? pages : []).map((p) => p?.page_idx).filter(Number.isInteger));
    const regions = Array.isArray(question?.regions) ? question.regions : [];
    for (const region of regions) {
      if (Number.isInteger(region?.page_idx) && (!validPages.size || validPages.has(region.page_idx))) {
        return region.page_idx;
      }
    }
    return pages?.[0]?.page_idx ?? null;
  }

  /**
   * 计算三步流程进度条状态
   * @param {object|null} paper 试卷对象
   * @param {object} counts 统计计数（all, approved, needAttention, ...）
   * @returns {{ activeStep: number, steps: Array<{ step: number, key: string, label: string, hint: string, status: "done"|"active"|"pending" }> }}
   */
  function computeStepperState(paper, counts = {}) {
    const total = Number(counts.all) || 0;
    const approved = Number(counts.approved) || 0;
    const isProcessing = Boolean(paper && ACTIVE_STATUSES.has(paper.status));
    const cutDone = Boolean(paper && !isProcessing && paper.status !== "needs_grouping" && total > 0);
    const reviewDone = Boolean(cutDone && approved >= total);

    let activeStep = 1;
    if (reviewDone) activeStep = 3;
    else if (cutDone) activeStep = 2;

    const steps = [
      {
        step: 1,
        key: "cut",
        label: "① 导卷切题",
        hint: paper?.pages?.length ? `共 ${paper.pages.length} 页` : "上传或整理原卷",
        status: cutDone ? "done" : "active",
      },
      {
        step: 2,
        key: "review",
        label: "② 逐题核对",
        hint: total ? `已通过 ${approved}/${total}` : "等待生成题卡",
        status: reviewDone ? "done" : cutDone ? "active" : "pending",
      },
      {
        step: 3,
        key: "archive",
        label: "③ 完成归档",
        hint: paper?.archived ? "已归档" : reviewDone ? "可归档或去题库" : "全部通过后完成",
        status: paper?.archived ? "done" : reviewDone ? "active" : "pending",
      },
    ];
    return { activeStep, steps };
  }

  /**
   * 创建左侧常驻原卷分屏画布控制器
   * @param {object} host 宿主回调集合
   * @returns {object} 控制器接口
   */
  function createController(host) {
    const {
      $,
      el,
      getPaper,
      getQuestions,
      getCurrentId,
      onSelectQuestion,
      onEditRegions,
      onOpenCutDialog,
      onSwitchToManual,
    } = host;

    let open = true;
    let showBoxes = true;
    let pageIdx = 0;
    let zoom = 1;
    let paperId = null;

    function isOpen() {
      return open;
    }

    function setOpen(nextOpen, { render = true } = {}) {
      open = Boolean(nextOpen);
      const workspace = $("paperWorkspace");
      const pane = $("intakeSplitPane");
      const toggle = $("splitCanvasToggle");
      if (workspace) workspace.classList.toggle("split-open", open);
      if (pane) pane.hidden = !open;
      if (toggle) {
        toggle.setAttribute("aria-pressed", String(open));
        const label = toggle.querySelector(".tool-label");
        if (label) label.textContent = open ? "收起原卷" : "对照原卷";
      }
      if (render && open) {
        renderCanvas();
        if (typeof requestAnimationFrame === "function") {
          requestAnimationFrame(() => {
            const sheetImg = $("splitCanvasStage")?.querySelector?.(".split-canvas-image");
            if (sheetImg && typeof sheetImg.decode === "function") {
              void sheetImg.decode().catch(() => {});
            }
          });
        }
      }
    }

    function setPage(nextPage, { syncRender = true } = {}) {
      const paper = getPaper();
      const pages = Array.isArray(paper?.pages) ? paper.pages : [];
      if (!pages.length) return;
      const clamped = Math.max(0, Math.min(pages.length - 1, Number(nextPage) || 0));
      pageIdx = pages[clamped]?.page_idx ?? clamped;
      if (syncRender && open) renderCanvas();
    }

    function focusQuestionOnCanvas(question) {
      const paper = getPaper();
      const targetPage = findFirstPageForQuestion(question, paper?.pages || []);
      if (targetPage !== null && targetPage !== pageIdx) {
        pageIdx = targetPage;
      }
      if (open) {
        renderCanvas({ scrollActiveIntoView: true });
      }
    }

    function syncPaper(paper) {
      if (!paper) {
        paperId = null;
        pageIdx = 0;
        renderCanvas();
        return;
      }
      if (paper.id !== paperId) {
        paperId = paper.id;
        zoom = 1;
        const currentQ = (getQuestions() || []).find((q) => q.id === getCurrentId());
        const preferred = findFirstPageForQuestion(currentQ, paper.pages || []);
        pageIdx = preferred !== null ? preferred : (paper.pages?.[0]?.page_idx ?? 0);
      } else if (Array.isArray(paper.pages) && paper.pages.length && !paper.pages.some((p) => p.page_idx === pageIdx)) {
        pageIdx = paper.pages[0].page_idx;
      }
      if (open) renderCanvas();
    }

    function renderCanvas({ scrollActiveIntoView = false } = {}) {
      const pane = $("intakeSplitPane");
      if (!pane || pane.hidden) return;
      const paper = getPaper();
      const pages = Array.isArray(paper?.pages) ? paper.pages : [];
      const stage = $("splitCanvasStage");
      const indicator = $("splitPageIndicator");
      const prevBtn = $("splitPagePrev");
      const nextBtn = $("splitPageNext");
      const zoomLabel = $("splitZoomLabel");
      const cutBtn = $("splitOpenCutDialog");
      const boxBtn = $("splitBoxesToggle");

      if (boxBtn) {
        boxBtn.setAttribute("aria-pressed", String(showBoxes));
        boxBtn.textContent = showBoxes ? "隐藏题框" : "显示题框";
      }
      if (zoomLabel) zoomLabel.textContent = `${Math.round(zoom * 100)}%`;

      if (!paper || !pages.length) {
        if (indicator) indicator.textContent = "等待原卷";
        if (prevBtn) prevBtn.disabled = true;
        if (nextBtn) nextBtn.disabled = true;
        if (cutBtn) cutBtn.disabled = !paper;
        if (stage) {
          const placeholder = el("div", "split-canvas-empty");
          placeholder.append(
            el("p", "split-canvas-empty-title", paper ? "原卷页面正在生成中" : "请选择或上传一份资料"),
            el(
              "p",
              "hint",
              paper
                ? "多张照片或文档正在整理页序；若想跳过等待直接框选，可点击下方按钮。"
                : "左侧栏支持上传 PDF、Word 与多张试卷照片。"
            )
          );
          if (paper && ["queued", "parsing", "failed"].includes(paper.status) && typeof onSwitchToManual === "function") {
            const cta = el("button", "button small primary", "立即本机准备原卷并切题");
            cta.type = "button";
            cta.addEventListener("click", () => onSwitchToManual());
            placeholder.append(cta);
          }
          stage.replaceChildren(placeholder);
        }
        return;
      }

      const pos = Math.max(0, pages.findIndex((p) => p.page_idx === pageIdx));
      const activePage = pages[pos] || pages[0];
      pageIdx = activePage.page_idx;

      if (indicator) indicator.textContent = `第 ${pos + 1} / ${pages.length} 页`;
      if (prevBtn) prevBtn.disabled = pos <= 0;
      if (nextBtn) nextBtn.disabled = pos >= pages.length - 1;
      if (cutBtn) cutBtn.disabled = false;

      if (!stage) return;

      const sheet = el("div", "split-canvas-sheet");
      sheet.style.width = `${Math.round(zoom * 100)}%`;

      const img = el("img", "split-canvas-image");
      img.alt = `${paper.name || "原卷"} 第 ${pos + 1} 页`;
      const ver = paper.pages_version ? `?v=${encodeURIComponent(paper.pages_version)}` : "";
      img.loading = "eager";
      img.decoding = "sync";
      img.src = `/api/papers/${paper.id}/pages/${pageIdx}/preview${ver}`;
      img.draggable = false;
      sheet.append(img);

      let activeBoxNode = null;
      if (showBoxes) {
        const overlay = el("div", "split-canvas-overlay");
        const boxes = pageBoxesForPage(getQuestions() || [], pageIdx, getCurrentId());
        for (const box of boxes) {
          const btn = el("button", `split-box tone-${box.tone}`);
          btn.type = "button";
          btn.dataset.splitBoxQid = String(box.questionId);
          btn.style.left = `${box.rect.left}%`;
          btn.style.top = `${box.rect.top}%`;
          btn.style.width = `${box.rect.width}%`;
          btn.style.height = `${box.rect.height}%`;
          btn.title = `${box.label}（单击定位右卡，双击调整原卷范围）`;

          const tag = el("span", "split-box-tag", box.label);
          btn.append(tag);

          btn.addEventListener("click", (event) => {
            event.stopPropagation();
            if (typeof onSelectQuestion === "function") onSelectQuestion(box.questionId);
          });
          btn.addEventListener("dblclick", (event) => {
            event.stopPropagation();
            if (typeof onEditRegions === "function") onEditRegions(box.questionId, pageIdx, box.regionIndex);
          });
          if (box.tone === "active") activeBoxNode = btn;
          overlay.append(btn);
        }
        sheet.append(overlay);
      }

      stage.replaceChildren(sheet);
      if (scrollActiveIntoView && activeBoxNode && typeof activeBoxNode.scrollIntoView === "function") {
        requestAnimationFrame(() => {
          activeBoxNode.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
        });
      }
    }

    function bindEvents() {
      $("splitCanvasToggle")?.addEventListener("click", () => setOpen(!open));
      $("splitCanvasClose")?.addEventListener("click", () => setOpen(false));
      $("splitPagePrev")?.addEventListener("click", () => {
        const paper = getPaper();
        const pages = paper?.pages || [];
        const pos = pages.findIndex((p) => p.page_idx === pageIdx);
        if (pos > 0) setPage(pos - 1);
      });
      $("splitPageNext")?.addEventListener("click", () => {
        const paper = getPaper();
        const pages = paper?.pages || [];
        const pos = pages.findIndex((p) => p.page_idx === pageIdx);
        if (pos >= 0 && pos < pages.length - 1) setPage(pos + 1);
      });
      $("splitZoomOut")?.addEventListener("click", () => {
        zoom = Math.max(0.6, Math.round((zoom - 0.15) * 100) / 100);
        renderCanvas();
      });
      $("splitZoomIn")?.addEventListener("click", () => {
        zoom = Math.min(2.4, Math.round((zoom + 0.15) * 100) / 100);
        renderCanvas();
      });
      $("splitZoomFit")?.addEventListener("click", () => {
        zoom = 1;
        renderCanvas();
      });
      $("splitBoxesToggle")?.addEventListener("click", () => {
        showBoxes = !showBoxes;
        renderCanvas();
      });
      $("splitOpenCutDialog")?.addEventListener("click", () => {
        if (typeof onOpenCutDialog === "function") onOpenCutDialog(pageIdx);
      });
    }

    return {
      isOpen,
      setOpen,
      setPage,
      syncPaper,
      renderCanvas,
      focusQuestionOnCanvas,
      bindEvents,
      getPageIdx: () => pageIdx,
    };
  }

  return {
    boxRectPercent,
    questionBoxTone,
    pageBoxesForPage,
    findFirstPageForQuestion,
    computeStepperState,
    createController,
  };
});
