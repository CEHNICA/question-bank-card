/*
 * Eagle 题库工作台：万题级连续虚拟滚动与数学公式排版缓存引擎。
 *
 * 设计目标：
 *   1. 连续丝滑滚动，不拆生硬的「上一页 / 下一页」；可视区上下仅挂载少量题卡（默认约 16~24 张），
 *      滚远的 DOM 节点即时回收，滚回瞬间按增量 diff 恢复。
 *   2. 缓存每道题已完成 KaTeX 排版的题面与解析 HTML（LRU 上限可控）；二次滚入直接注入已排好公式 HTML，
 *      零重复调用 renderMathInElement，快速拖动万题滚动条不发热不掉帧。
 *   3. 动态实测高度记忆 + 向上滚动锚点补偿（Scroll Anchoring），高矮不一的图文/公式卡片不跳动。
 *   4. 实时回报「当前第 X / 共 N 题」位置，并支持按 1-based 题序或题目 ID 毫秒级直达。
 */
(function (root, factory) {
  "use strict";
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.EagleVirtualList = factory();
  }
})(typeof globalThis !== "undefined" ? globalThis : window, function () {
  "use strict";

  const DEFAULT_CARD_HEIGHT = 188;
  const DEFAULT_COMPACT_HEIGHT = 46;
  const CARD_GAP = 8;
  const COMPACT_GAP = 6;
  const DEFAULT_OVERSCAN = 6;
  const DEFAULT_FORMULA_CACHE_LIMIT = 4000;

  function createFormulaCache(maxEntries = DEFAULT_FORMULA_CACHE_LIMIT) {
    const limit = Math.max(64, Number(maxEntries) || DEFAULT_FORMULA_CACHE_LIMIT);
    const map = new Map();

    function get(key) {
      if (!map.has(key)) return null;
      const value = map.get(key);
      map.delete(key);
      map.set(key, value);
      return value;
    }

    function set(key, html) {
      if (!key || typeof html !== "string") return;
      if (map.has(key)) map.delete(key);
      map.set(key, html);
      while (map.size > limit) {
        const oldest = map.keys().next().value;
        map.delete(oldest);
      }
    }

    function invalidatePrefix(prefix) {
      if (!prefix) return;
      for (const key of Array.from(map.keys())) {
        if (key.startsWith(prefix)) map.delete(key);
      }
    }

    function clear() {
      map.clear();
    }

    return {
      get,
      set,
      invalidatePrefix,
      clear,
      get size() {
        return map.size;
      }
    };
  }

  function questionStemCacheKey(q, viewMode) {
    if (!q || !q.id) return "";
    const ver = q.version || 1;
    const body = viewMode === "compact" ? String(q.rawStem || "") : String(q.content || "");
    return `stem:${viewMode}:${q.id}:v${ver}:l${body.length}:${body.slice(0, 48)}`;
  }

  function questionAnalysisCacheKey(q) {
    if (!q || !q.id) return "";
    const sol = q.savedSolution;
    const solSig = sol
      ? `s:${sol.id || ""}:${(sol.answer || "").length}:${(sol.analysis || "").length}`
      : q.aiAnswer
        ? `ai:${(q.aiAnswer.answer || "").length}:${(q.aiAnswer.analysis || "").length}`
        : `orig:${(q.rawAnswer || "").length}:${(q.rawAnalysis || "").length}`;
    return `ana:${q.id}:${solSig}`;
  }

  function clampIndex(oneBasedIndex, total) {
    const n = Math.max(0, Number(total) || 0);
    if (n === 0) return 0;
    const parsed = Math.floor(Number(oneBasedIndex));
    if (!Number.isFinite(parsed)) return 1;
    return Math.max(1, Math.min(n, parsed));
  }

  function heightKey(q, viewMode, isExpanded) {
    return `${viewMode}:${q.id}:${isExpanded ? 1 : 0}`;
  }

  function defaultItemHeight(viewMode) {
    return viewMode === "compact" ? DEFAULT_COMPACT_HEIGHT : DEFAULT_CARD_HEIGHT;
  }

  function itemGap(viewMode) {
    return viewMode === "compact" ? COMPACT_GAP : CARD_GAP;
  }

  function buildOffsets(items, viewMode, heightMap, isExpandedFn) {
    const n = items.length;
    const offsets = new Float64Array(n + 1);
    const heights = new Float64Array(n);
    if (n === 0) return { offsets, heights, totalHeight: 0 };

    const fallback = defaultItemHeight(viewMode);
    const gap = itemGap(viewMode);
    let y = 0;
    for (let i = 0; i < n; i++) {
      offsets[i] = y;
      const q = items[i];
      const expanded = typeof isExpandedFn === "function" ? Boolean(isExpandedFn(q)) : false;
      const h = heightMap.get(heightKey(q, viewMode, expanded)) || fallback;
      heights[i] = h;
      y += h + (i + 1 < n ? gap : 0);
    }
    offsets[n] = y;
    return { offsets, heights, totalHeight: y };
  }

  function findIndexAtScrollTop(offsets, count, scrollTop) {
    if (count <= 0) return -1;
    const target = Math.max(0, Number(scrollTop) || 0);
    let lo = 0;
    let hi = count - 1;
    let ans = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >>> 1;
      if (offsets[mid] <= target + 4) {
        ans = mid;
        lo = mid + 1;
      } else {
        hi = mid - 1;
      }
    }
    return ans;
  }

  function computeWindowRange(offsets, count, scrollTop, viewportHeight, overscan = DEFAULT_OVERSCAN) {
    if (count <= 0) {
      return { start: 0, end: -1, visibleFirst: 0 };
    }
    const vh = Math.max(240, Number(viewportHeight) || 720);
    const firstVis = findIndexAtScrollTop(offsets, count, scrollTop);
    const bottomEdge = Math.max(0, Number(scrollTop) || 0) + vh;
    let lastVis = firstVis;
    while (lastVis + 1 < count && offsets[lastVis + 1] < bottomEdge) {
      lastVis += 1;
    }
    const pad = Math.max(2, Number(overscan) || DEFAULT_OVERSCAN);
    const start = Math.max(0, firstVis - pad);
    const end = Math.min(count - 1, lastVis + pad);
    return { start, end, visibleFirst: firstVis };
  }

  function createVirtualListController(options = {}) {
    const formulaCache = options.formulaCache || createFormulaCache(options.formulaCacheLimit);
    const heightMap = new Map();
    const overscan = options.overscan ?? DEFAULT_OVERSCAN;

    let items = [];
    let viewMode = "card";
    let layout = { offsets: new Float64Array(1), heights: new Float64Array(0), totalHeight: 0 };
    const mountedByIndex = new Map();
    let mountedStart = 0;
    let mountedEnd = -1;
    let rafHandle = null;
    let resizeObserver = null;
    let currentVisibleOneBased = 0;

    const getContainer = () =>
      typeof options.getContainer === "function" ? options.getContainer() : null;
    const isExpanded = (q) =>
      typeof options.isAnalysisExpanded === "function" ? Boolean(options.isAnalysisExpanded(q.id)) : false;
    const renderCardHtml = (q, mode) =>
      typeof options.renderItemHtml === "function" ? options.renderItemHtml(q, mode) : "";
    const runKaTeX = (el) => {
      if (typeof options.triggerKaTeX === "function") options.triggerKaTeX(el);
    };
    const onPositionChange = (currentOneBased, total) => {
      if (typeof options.onPositionChange === "function") {
        options.onPositionChange(currentOneBased, total);
      }
    };

    function ensureResizeObserver() {
      if (resizeObserver || typeof ResizeObserver !== "function") return;
      resizeObserver = new ResizeObserver(() => {
        measureMountedCards(true);
      });
    }

    function ensureStructure(container) {
      let topSpacer = container.querySelector("#virtualTopSpacer");
      let host = container.querySelector("#virtualCardsHost");
      let bottomSpacer = container.querySelector("#virtualBottomSpacer");
      const sentinel = container.querySelector("#chunkSentinel");
      const doc = container.ownerDocument;

      if (!topSpacer || !host || !bottomSpacer) {
        topSpacer = doc.createElement("div");
        topSpacer.id = "virtualTopSpacer";
        topSpacer.setAttribute("aria-hidden", "true");
        topSpacer.style.cssText = "height:0px;margin:0 !important;padding:0;flex-shrink:0;";

        host = doc.createElement("div");
        host.id = "virtualCardsHost";
        host.style.marginTop = "0";
        host.style.marginBottom = "0";

        bottomSpacer = doc.createElement("div");
        bottomSpacer.id = "virtualBottomSpacer";
        bottomSpacer.setAttribute("aria-hidden", "true");
        bottomSpacer.style.cssText = "height:0px;margin:0 !important;padding:0;flex-shrink:0;";

        if (sentinel && sentinel.parentNode === container) {
          container.insertBefore(topSpacer, sentinel);
          container.insertBefore(host, sentinel);
          container.insertBefore(bottomSpacer, sentinel);
        } else {
          container.appendChild(topSpacer);
          container.appendChild(host);
          container.appendChild(bottomSpacer);
        }
      }
      host.className = viewMode === "card" ? "space-y-2" : "space-y-1.5";
      return { topSpacer, host, bottomSpacer };
    }

    function hydrateCardMathWithCache(cardEl, q) {
      if (!cardEl || !q) return;
      const stemEl = cardEl.querySelector(".eagle-q-stem, .eagle-compact-stem");
      const stemKey = questionStemCacheKey(q, viewMode);
      const cachedStem = stemKey ? formulaCache.get(stemKey) : null;

      const anaBox = cardEl.querySelector(`#analysis_box_${q.id}`);
      const expanded = isExpanded(q);
      const anaKey = expanded ? questionAnalysisCacheKey(q) : "";
      const cachedAna = anaKey ? formulaCache.get(anaKey) : null;

      if (cachedStem !== null && stemEl) {
        stemEl.innerHTML = cachedStem;
      }
      if (expanded && anaBox && cachedAna !== null) {
        anaBox.innerHTML = cachedAna;
        anaBox.dataset.katexDone = "true";
      }

      const needStemMath = Boolean(stemEl && cachedStem === null);
      const needAnaMath = Boolean(expanded && anaBox && cachedAna === null);

      if (needStemMath || needAnaMath) {
        runKaTeX(cardEl);
        if (needStemMath && stemEl && stemKey) {
          formulaCache.set(stemKey, stemEl.innerHTML);
        }
        if (needAnaMath && anaBox && anaKey) {
          anaBox.dataset.katexDone = "true";
          formulaCache.set(anaKey, anaBox.innerHTML);
        }
      }
    }

    function buildCardElement(doc, q) {
      const temp = doc.createElement("div");
      temp.innerHTML = renderCardHtml(q, viewMode).trim();
      const cardEl = temp.firstElementChild;
      if (!cardEl) return null;
      hydrateCardMathWithCache(cardEl, q);
      ensureResizeObserver();
      if (resizeObserver) resizeObserver.observe(cardEl);
      return cardEl;
    }

    function updateSpacers(container) {
      const topSpacer = container.querySelector("#virtualTopSpacer");
      const bottomSpacer = container.querySelector("#virtualBottomSpacer");
      if (!topSpacer || !bottomSpacer) return;
      const n = items.length;
      if (n === 0 || mountedEnd < mountedStart) {
        topSpacer.style.height = "0px";
        bottomSpacer.style.height = "0px";
        return;
      }
      const gap = itemGap(viewMode);
      const topH = Math.max(0, layout.offsets[mountedStart]);
      const mountedBottom = layout.offsets[mountedEnd] + layout.heights[mountedEnd];
      const botH = Math.max(0, layout.totalHeight - mountedBottom + (mountedEnd < n - 1 ? gap : 0));
      topSpacer.style.height = `${Math.round(topH)}px`;
      bottomSpacer.style.height = `${Math.round(botH)}px`;
    }

    function measureMountedCards(adjustScrollAnchor = false) {
      const container = getContainer();
      if (!container || mountedByIndex.size === 0) return;

      const scrollTopBefore = container.scrollTop || 0;
      const firstVisibleIdx = findIndexAtScrollTop(layout.offsets, items.length, scrollTopBefore);
      let changed = false;
      let deltaAboveAnchor = 0;

      for (const [idx, el] of mountedByIndex.entries()) {
        if (idx < 0 || idx >= items.length || !el || !el.isConnected) continue;
        const rectH = el.offsetHeight || (typeof el.getBoundingClientRect === "function" ? el.getBoundingClientRect().height : 0);
        if (rectH > 12) {
          const q = items[idx];
          const key = heightKey(q, viewMode, isExpanded(q));
          const prevH = layout.heights[idx] || defaultItemHeight(viewMode);
          if (Math.abs(rectH - prevH) >= 1.5) {
            heightMap.set(key, rectH);
            changed = true;
            if (adjustScrollAnchor && idx < firstVisibleIdx) {
              deltaAboveAnchor += rectH - prevH;
            }
          }
        }
      }

      if (changed) {
        layout = buildOffsets(items, viewMode, heightMap, isExpanded);
        updateSpacers(container);
        if (adjustScrollAnchor && Math.abs(deltaAboveAnchor) >= 1.5) {
          container.scrollTop = Math.max(0, scrollTopBefore + deltaAboveAnchor);
        }
      }
    }

    function syncWindow(forceRemount = false) {
      const container = getContainer();
      if (!container) return;
      if (items.length === 0) {
        mountedByIndex.clear();
        mountedStart = 0;
        mountedEnd = -1;
        currentVisibleOneBased = 0;
        onPositionChange(0, 0);
        return;
      }

      const { host } = ensureStructure(container);
      const scrollTop = container.scrollTop || 0;
      const viewportH = container.clientHeight || 720;
      const range = computeWindowRange(layout.offsets, items.length, scrollTop, viewportH, overscan);
      const nextStart = range.start;
      const nextEnd = range.end;
      const doc = container.ownerDocument;

      if (forceRemount) {
        if (resizeObserver) resizeObserver.disconnect();
        host.innerHTML = "";
        mountedByIndex.clear();
      } else {
        for (const [idx, el] of Array.from(mountedByIndex.entries())) {
          if (idx < nextStart || idx > nextEnd) {
            if (resizeObserver) resizeObserver.unobserve(el);
            if (el.parentNode === host) host.removeChild(el);
            mountedByIndex.delete(idx);
          }
        }
      }

      // Mount missing indices in order, preserving untouched existing DOM nodes
      let cursorNode = host.firstElementChild;
      for (let i = nextStart; i <= nextEnd; i++) {
        const existing = mountedByIndex.get(i);
        if (existing && existing.parentNode === host) {
          cursorNode = existing.nextElementSibling;
          continue;
        }
        const created = buildCardElement(doc, items[i]);
        if (!created) continue;
        if (cursorNode && cursorNode.parentNode === host) {
          host.insertBefore(created, cursorNode);
        } else {
          host.appendChild(created);
        }
        mountedByIndex.set(i, created);
      }

      mountedStart = nextStart;
      mountedEnd = nextEnd;
      updateSpacers(container);
      measureMountedCards(true);

      const nextOneBased = clampIndex(range.visibleFirst + 1, items.length);
      if (nextOneBased !== currentVisibleOneBased || forceRemount) {
        currentVisibleOneBased = nextOneBased;
        onPositionChange(nextOneBased, items.length);
      }
    }

    function onScroll() {
      if (rafHandle != null) return;
      const schedule = typeof requestAnimationFrame === "function" ? requestAnimationFrame : (fn) => setTimeout(fn, 16);
      rafHandle = schedule(() => {
        rafHandle = null;
        syncWindow(false);
      });
    }

    function setItems(nextItems, nextViewMode, optionsReset = {}) {
      items = Array.isArray(nextItems) ? nextItems : [];
      if (nextViewMode) viewMode = nextViewMode;
      layout = buildOffsets(items, viewMode, heightMap, isExpanded);
      const container = getContainer();
      if (container && optionsReset.resetScroll) {
        container.scrollTop = 0;
      }
      syncWindow(Boolean(optionsReset.forceRemount));
      onPositionChange(items.length ? clampIndex(currentVisibleOneBased || 1, items.length) : 0, items.length);
    }

    function invalidateQuestion(qid) {
      const target = String(qid || "");
      if (!target) return;
      formulaCache.invalidatePrefix(`stem:card:${target}:`);
      formulaCache.invalidatePrefix(`stem:compact:${target}:`);
      formulaCache.invalidatePrefix(`ana:${target}:`);
      for (const mode of ["card", "compact"]) {
        heightMap.delete(`${mode}:${target}:0`);
        heightMap.delete(`${mode}:${target}:1`);
      }
    }

    function cacheExpandedAnalysis(q, anaBoxEl) {
      if (!q || !anaBoxEl) return;
      const key = questionAnalysisCacheKey(q);
      if (key) formulaCache.set(key, anaBoxEl.innerHTML);
      measureMountedCards(false);
    }

    function refreshSingleQuestion(qid) {
      const target = String(qid || "");
      invalidateQuestion(target);
      const idx = items.findIndex((q) => String(q.id) === target);
      if (idx < 0) return false;
      const container = getContainer();
      const oldEl = mountedByIndex.get(idx);
      if (!container || !oldEl || !oldEl.parentNode) {
        layout = buildOffsets(items, viewMode, heightMap, isExpanded);
        return false;
      }
      const nextEl = buildCardElement(container.ownerDocument, items[idx]);
      if (!nextEl) return false;
      if (resizeObserver) resizeObserver.unobserve(oldEl);
      oldEl.parentNode.replaceChild(nextEl, oldEl);
      mountedByIndex.set(idx, nextEl);
      measureMountedCards(false);
      return true;
    }

    function scrollToOneBasedIndex(oneBasedIndex) {
      const container = getContainer();
      if (!container || items.length === 0) return null;
      const clamped = clampIndex(oneBasedIndex, items.length);
      const idx = clamped - 1;
      layout = buildOffsets(items, viewMode, heightMap, isExpanded);
      container.scrollTop = Math.max(0, Math.round(layout.offsets[idx]));
      syncWindow(false);
      // Settle once more after real DOM height of target card is measured
      const actualTop = layout.offsets[idx];
      if (Math.abs((container.scrollTop || 0) - actualTop) > 2) {
        container.scrollTop = Math.max(0, Math.round(actualTop));
        syncWindow(false);
      }
      currentVisibleOneBased = clamped;
      onPositionChange(clamped, items.length);
      return items[idx] || null;
    }

    function scrollToQuestionId(qid) {
      const target = String(qid || "");
      const idx = items.findIndex((q) => String(q.id) === target);
      if (idx < 0) return false;
      scrollToOneBasedIndex(idx + 1);
      return Boolean(mountedByIndex.get(idx));
    }

    return {
      setItems,
      syncWindow,
      onScroll,
      invalidateQuestion,
      cacheExpandedAnalysis,
      refreshSingleQuestion,
      scrollToOneBasedIndex,
      scrollToQuestionId,
      measureMountedCards,
      get formulaCache() {
        return formulaCache;
      },
      get mountedCount() {
        return mountedByIndex.size;
      },
      get currentOneBased() {
        return currentVisibleOneBased;
      }
    };
  }

  return {
    createFormulaCache,
    createVirtualListController,
    questionStemCacheKey,
    questionAnalysisCacheKey,
    buildOffsets,
    findIndexAtScrollTop,
    computeWindowRange,
    clampIndex
  };
});
