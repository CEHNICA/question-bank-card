/**
 * Eagle B5：答案解析编辑器
 * 双栏实时预览 / 顶部多题标签 / 多题并排对比 / 完整题面（含配图与选项） / 光标与公式高亮联动 / 原卷裁图 / AI 初稿生成与对照
 */
(function () {
  const MAX_COLUMNS = 6;
  const AI_POLL_INTERVAL_MS = 1500;
  const AI_POLL_MAX_ATTEMPTS = 60;

  const SOL = {
    open: false,
    search: '',
    order: [],
    activeId: null,
    viewMode: 'split',
    lastField: 'analysis',
    cols: new Map(),
    crops: new Map(),
    pollTimers: new Map(),
    delegationBound: false,
    cropBound: false,
    shortcutsBound: false,
  };

  const $el = (id) => document.getElementById(id);
  // 宿主 index.html 中 API_BASE、questionsData、filteredQuestions、basketQuestionIds 使用顶层 const/let 声明，不会挂到 window 上，因此优先读取词法作用域变量
  const apiBase = () => (typeof API_BASE === 'string' ? API_BASE : (typeof window.API_BASE === 'string' ? window.API_BASE : ''));
  const qbApi = () => (typeof QBRender !== 'undefined' ? QBRender : (typeof QB !== 'undefined' ? QB : (window.QBRender || window.QB || null)));
  const allItems = () => (typeof questionsData !== 'undefined' && Array.isArray(questionsData) ? questionsData : (Array.isArray(window.questionsData) ? window.questionsData : []));
  const filteredList = () => (typeof filteredQuestions !== 'undefined' && Array.isArray(filteredQuestions) ? filteredQuestions : (Array.isArray(window.filteredQuestions) ? window.filteredQuestions : []));
  const basketIdsList = () => {
    const raw = typeof basketQuestionIds !== 'undefined' ? basketQuestionIds : window.basketQuestionIds;
    if (raw instanceof Set) return Array.from(raw);
    return Array.isArray(raw) ? raw : [];
  };
  const findItem = (id) => allItems().find((q) => String(q.id) === String(id)) || null;
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));

  /**
   * 构造统一请求头
   * @param {boolean} isJson 是否为 JSON 请求体
   * @returns {Record<string, string>} 请求头对象
   */
  function requestHeaders(isJson) {
    const headers = { 'X-QB-Request': '1' };
    if (isJson) headers['Content-Type'] = 'application/json';
    return headers;
  }

  /**
   * 规范化前端资源地址（支持相对路径拼接 API_BASE）
   * @param {string} rawUrl 原始地址
   * @returns {string} 可访问地址
   */
  function resolveAssetUrl(rawUrl) {
    const url = String(rawUrl || '').trim();
    if (!url) return '';
    if (/^(?:https?:|data:|blob:)/i.test(url)) return url;
    if (url.startsWith('/')) {
      const base = apiBase();
      return base ? `${base}${url}` : url;
    }
    return url;
  }

  /**
   * 复制并规整配图元数据，确保服务端与实时预览字段一致
   * @param {Array} figures 配图数组
   * @returns {Array} 规整后的配图数组
   */
  function cloneFigures(figures) {
    return (figures || []).map((f) => ({
      id: f.id,
      url: f.url || '',
      display_width: Number(f.display_width) || 54,
      position: f.position || 'after',
      paragraph: Number.isInteger(f.paragraph) ? f.paragraph : (f.paragraph != null && f.paragraph !== '' ? Number(f.paragraph) : null),
    }));
  }

  /**
   * 判断试题在题库中的既有答案解析来源状态
   * @param {object} q 试题对象
   * @returns {{ hasFormal: boolean, hasOrigin: boolean }} 状态对象
   */
  function hasSolutionStatus(q) {
    const s = q?.savedSolution || {};
    const hasFormal = Boolean((s.answer || '').trim() || (s.analysis || '').trim() || (s.figures && s.figures.length));
    const hasOrigin = Boolean((q?.rawAnswer || '').trim() || (q?.rawAnalysis || '').trim());
    return { hasFormal, hasOrigin };
  }

  /**
   * 格式化行内数学摘要文本，避免在公式内部被截断导致 $ 未闭合
   * @param {string} raw 原始题干文本
   * @returns {string} 安全可交由 KaTeX 渲染的 HTML
   */
  function formatInlineSummaryHtml(raw) {
    if (typeof window.getCleanInlineStem === 'function') {
      return window.getCleanInlineStem(raw);
    }
    const text = String(raw || '题目内容')
      .replace(/\$\$([\s\S]*?)\$\$/g, '$$$1$$')
      .replace(/\\\[([\s\S]*?)\\\]/g, '$$$1$$')
      .replace(/\\\(([\s\S]*?)\\\)/g, '$$$1$$')
      .replace(/[\r\n]+/g, ' ')
      .trim();
    return esc(text);
  }

  /**
   * 判断字符串是否为选择题纯选项答案（如 C、AB）
   * @param {string} answer 答案文本
   * @param {object} item 试题对象
   * @returns {boolean} 是否按选项高亮展示
   */
  function isChoiceLetterAnswer(answer, item) {
    const code = String(item?.questionTypeCode || item?.content?.question_type || '');
    const typeLabel = String(item?.questionType || '');
    const isChoiceType = ['choice', 'single_choice', 'multiple_choice'].includes(code) || /单选|多选|选择/.test(typeLabel);
    return isChoiceType && /^[A-F](?:[\s,、/]*[A-F])*$/i.test(String(answer || '').trim());
  }

  /**
   * 按空行分段解析文本并记录全局字符偏移，保证多段落光标定位与图文混排一致
   * @param {string} value 解析文本
   * @returns {Array<{ text: string, offset: number }>} 段落列表
   */
  function splitAnalysisParagraphs(value) {
    const source = String(value ?? '');
    const parts = [];
    const pattern = /\n\s*\n/g;
    let cursor = 0;
    let match;
    while ((match = pattern.exec(source)) !== null) {
      const chunk = source.slice(cursor, match.index);
      if (chunk.trim()) parts.push({ text: chunk, offset: cursor });
      cursor = match.index + match[0].length;
    }
    const tail = source.slice(cursor);
    if (tail.trim()) parts.push({ text: tail, offset: cursor });
    return parts;
  }

  /**
   * 兜底触发 KaTeX 渲染（当 QB.renderTypeset 不可用或存在未渲染兜底节点时）
   * @param {HTMLElement} node 目标容器
   */
  function applyKaTeXFallback(node) {
    if (!node) return;
    if (typeof window.triggerKaTeX === 'function') {
      window.triggerKaTeX(node);
    }
  }

  /**
   * 使用 QB.renderTypeset 渲染富文本公式块，若 QB 不可用则退回安全 HTML + triggerKaTeX
   * @param {HTMLElement} node 目标容器
   * @param {string} text 含公式文本
   * @param {object} [opts] 渲染选项
   */
  function renderFormulaTextNode(node, text, opts) {
    if (!node) return;
    const qb = qbApi();
    const options = opts || {};
    if (qb && typeof qb.renderTypeset === 'function') {
      qb.renderTypeset(node, text, options);
      // 若浏览器侧 katex 晚于 QB 初始化或存在兜底片段，确保再跑一次 KaTeX
      if (node.querySelector('.qb-math-fallback')) {
        applyKaTeXFallback(node);
      }
      return;
    }
    const emptyText = options.empty || '';
    if (!String(text || '').trim()) {
      node.innerHTML = emptyText ? `<span class="text-slate-400 italic">${esc(emptyText)}</span>` : '';
      return;
    }
    node.innerHTML = esc(text).replace(/\n/g, '<br>');
    applyKaTeXFallback(node);
  }

  /**
   * 构造传给 QB.renderQuestion 的标准题目结构
   * @param {object} item 工作台试题对象
   * @returns {object} 规范化题面对象
   */
  function buildQuestionContent(item) {
    if (!item) return { stem: '', options: {}, figures: [] };
    const options = (item.rawOptions && typeof item.rawOptions === 'object' && Object.keys(item.rawOptions).length)
      ? item.rawOptions
      : (item.content?.options || {});
    const rawFigs = (Array.isArray(item.rawFigures) && item.rawFigures.length)
      ? item.rawFigures
      : (item.figures || item.content?.figures || []);
    // 部分题库条目仅存 url/file 未显式带 slot，默认归为题干配图以确保 QB.renderQuestion 正常渲染
    const figures = rawFigs.map((f) => (f && typeof f === 'object' ? { ...f, slot: f.slot || 'stem' } : f));
    return {
      stem: item.rawStem ?? item.content?.stem ?? item.stem ?? '',
      options,
      figures,
      question_images: item.content?.question_images || item.question_images || [],
      body_mode: item.content?.body_mode || item.body_mode || '',
      question_type: item.questionTypeCode || item.content?.question_type || '',
      question_number: item.questionNumber || item.content?.question_number || '',
    };
  }

  /**
   * 绑定预览区内图片点击放大灯箱
   * @param {HTMLElement} container 预览容器
   * @param {string} title 灯箱标题
   */
  function bindImageZoomHandlers(container, title) {
    if (!container) return;
    container.querySelectorAll('img').forEach((img) => {
      if (img.dataset.zoomBound) return;
      img.dataset.zoomBound = '1';
      img.title = img.title || '点击查看大图';
      img.addEventListener('click', (e) => {
        e.stopPropagation();
        const src = img.currentSrc || img.src;
        if (!src) return;
        if (typeof window.previewImageModal === 'function') {
          window.previewImageModal(src, title || '题目配图预览');
        }
      });
    });
  }

  /**
   * 完整渲染题目原题面（题干公式 + 题干几何配图 + 选择题选项 A~F）
   * @param {HTMLElement} container 题面容器
   * @param {object} item 试题对象
   */
  function renderQuestionStemPreview(container, item) {
    if (!container) return;
    container.innerHTML = '';
    if (!item) {
      container.innerHTML = '<div class="text-xs text-slate-400">未找到题面信息</div>';
      return;
    }
    const qb = qbApi();
    const content = buildQuestionContent(item);
    if (qb && typeof qb.renderQuestion === 'function') {
      qb.renderQuestion(container, content, {
        showNumber: false,
        showAnswer: 'none',
        resolveUrl: (fig) => resolveAssetUrl(fig?.url || fig?.file || fig?.src || ''),
        resolveQuestionImageUrl: (img) => resolveAssetUrl(img?.url || img?.file || img?.src || ''),
      });
      if (container.querySelector('.qb-math-fallback')) {
        applyKaTeXFallback(container);
      }
    } else {
      const stemDiv = document.createElement('div');
      stemDiv.className = 'text-xs leading-relaxed text-slate-800 whitespace-pre-wrap';
      renderFormulaTextNode(stemDiv, content.stem, { empty: '暂无题干' });
      container.appendChild(stemDiv);
      const optEntries = Object.entries(content.options || {}).filter(([, val]) => String(val || '').trim());
      if (optEntries.length) {
        const list = document.createElement('div');
        list.className = 'mt-2 grid grid-cols-1 sm:grid-cols-2 gap-1.5 text-xs text-slate-700';
        optEntries.forEach(([key, val]) => {
          const row = document.createElement('div');
          row.className = 'flex items-baseline gap-1';
          const lbl = document.createElement('span');
          lbl.className = 'font-bold text-slate-500';
          lbl.textContent = `${key}.`;
          const body = document.createElement('span');
          renderFormulaTextNode(body, String(val));
          row.appendChild(lbl);
          row.appendChild(body);
          list.appendChild(row);
        });
        container.appendChild(list);
      }
      if (Array.isArray(content.figures) && content.figures.length) {
        const figRow = document.createElement('div');
        figRow.className = 'qb-figures';
        content.figures.forEach((f) => {
          const url = resolveAssetUrl(f?.url || f?.file || f?.src || '');
          if (!url) return;
          const fig = document.createElement('figure');
          fig.className = 'qb-figure';
          const img = document.createElement('img');
          img.src = url;
          img.alt = '题干配图';
          fig.appendChild(img);
          figRow.appendChild(fig);
        });
        container.appendChild(figRow);
      }
    }
    bindImageZoomHandlers(container, `第 ${item.questionNumber || '?'} 题 · 原题配图`);
  }

  /**
   * 创建预览用配图节点（按真实毫米宽度展示并支持点击放大）
   * @param {object} figure 配图对象
   * @param {number} index 配图序号（1 起）
   * @returns {HTMLElement|null} figure 元素
   */
  function buildPreviewFigureNode(figure, index) {
    const url = resolveAssetUrl(figure?.url);
    if (!url) return null;
    const widthMm = Math.max(5, Math.min(178, Number(figure.display_width) || 54));
    const box = document.createElement('figure');
    box.className = 'sol-preview-fig solution-figure';
    const img = document.createElement('img');
    img.src = url;
    img.alt = `解析配图 ${index}`;
    img.loading = 'eager';
    img.dataset.solutionWidth = String(widthMm);
    img.style.width = `${widthMm}mm`;
    img.style.maxWidth = '100%';
    img.style.height = 'auto';
    box.appendChild(img);
    const caption = document.createElement('figcaption');
    caption.className = 'text-[10px] text-slate-400 text-center mt-0.5';
    caption.textContent = `图${index} · ${widthMm}mm`;
    box.appendChild(caption);
    return box;
  }

  /**
   * 实时渲染当前题目的答案、解析段落与配图排版预览
   * @param {object} col 列状态对象
   * @param {HTMLElement} previewEl 预览挂载容器
   * @param {HTMLElement|null} hintEl 光标状态提示节点
   */
  function renderLiveSolutionPreview(col, previewEl, hintEl) {
    if (!col || !previewEl) return;
    const qb = qbApi();
    const answerText = String(col.value.answer || '');
    const analysisText = String(col.value.analysis || '');
    const figures = Array.isArray(col.value.figures) ? col.value.figures : [];
    const hasAny = Boolean(answerText.trim() || analysisText.trim() || figures.length);

    previewEl.innerHTML = '';
    if (!hasAny) {
      previewEl.innerHTML = '<div class="py-8 text-center text-xs text-slate-400 leading-relaxed">尚未填写答案或解析。<br>可在左侧输入 LaTeX 公式（如 <code class="px-1 py-0.5 bg-slate-100 rounded text-slate-600">$\\frac{1}{2}$</code>），点击 AI 初稿一键采纳，或按 <code class="px-1 py-0.5 bg-slate-100 rounded text-slate-600">Ctrl+V</code> 直接粘贴截图配图。</div>';
      if (hintEl && !hintEl.dataset.locked) {
        hintEl.textContent = '实时同步左侧编辑内容';
      }
      return;
    }

    // 1. 答案区（带 data-qb-field="answer" 以支持小问分解与光标定位）
    if (answerText.trim()) {
      const sec = document.createElement('div');
      sec.className = 'sol-preview-section';
      const label = document.createElement('div');
      label.className = 'text-[11px] font-bold text-emerald-700 mb-1';
      label.textContent = '【答案】';
      sec.appendChild(label);

      const parts = qb && typeof qb.subQuestionParts === 'function' ? qb.subQuestionParts(answerText) : null;
      if (parts && parts.length) {
        const list = document.createElement('div');
        list.className = 'qb-subquestions text-slate-800';
        list.dataset.qbField = 'answer';
        parts.forEach((part) => {
          const item = document.createElement('div');
          item.className = 'qb-subquestion';
          if (part.lead) {
            const lead = document.createElement('span');
            lead.className = 'qb-subquestion-lead';
            renderFormulaTextNode(lead, part.lead, { trackSource: true, sourceOffset: 0 });
            item.appendChild(lead);
          }
          const lbl = document.createElement('span');
          lbl.className = 'qb-subquestion-label';
          lbl.textContent = part.label;
          item.appendChild(lbl);
          const body = document.createElement('span');
          body.className = 'qb-subquestion-body';
          renderFormulaTextNode(body, part.body, { trackSource: true, sourceOffset: part.offset });
          item.appendChild(body);
          list.appendChild(item);
        });
        sec.appendChild(list);
      } else {
        const ansBody = document.createElement('div');
        ansBody.className = isChoiceLetterAnswer(answerText, col.item)
          ? 'qb-choice-answer text-base leading-relaxed'
          : 'sol-rendered-text text-slate-800';
        ansBody.dataset.qbField = 'answer';
        renderFormulaTextNode(ansBody, answerText, { trackSource: true, sourceOffset: 0 });
        sec.appendChild(ansBody);
      }
      previewEl.appendChild(sec);
    }

    // 2. 解析区与配图排版（before / paragraph / after）
    const figIndexMap = new Map(figures.map((f, idx) => [f, idx + 1]));
    const appendFigGroup = (target, list) => {
      if (!list.length) return;
      const row = document.createElement('div');
      row.className = 'sol-preview-figures';
      list.forEach((f) => {
        const node = buildPreviewFigureNode(f, figIndexMap.get(f) || 1);
        if (node) row.appendChild(node);
      });
      if (row.children.length) target.appendChild(row);
    };

    const beforeFigs = figures.filter((f) => f.position === 'before');
    const analysisParts = splitAnalysisParagraphs(analysisText);
    const afterAndOverflowFigs = figures.filter((f) => {
      if (f.position === 'after') return true;
      if (f.position === 'paragraph') {
        const paraIdx = Number.isInteger(f.paragraph) ? f.paragraph : 0;
        return paraIdx >= analysisParts.length;
      }
      return f.position !== 'before';
    });

    if (beforeFigs.length || analysisParts.length || afterAndOverflowFigs.length) {
      const sec = document.createElement('div');
      sec.className = 'sol-preview-section';
      const label = document.createElement('div');
      label.className = 'text-[11px] font-bold text-slate-700 mb-1';
      label.textContent = '【解析】';
      sec.appendChild(label);

      appendFigGroup(sec, beforeFigs);

      if (analysisParts.length) {
        // 单一 data-qb-field="analysis" 宿主节点，保证多段落源码偏移均能准确投射光标
        const analysisHost = document.createElement('div');
        analysisHost.className = 'qb-analysis-field space-y-2';
        analysisHost.dataset.qbField = 'analysis';
        analysisParts.forEach((part, idx) => {
          const paraNode = document.createElement('div');
          paraNode.className = 'qb-analysis solution-paragraph text-slate-800';
          renderFormulaTextNode(paraNode, part.text, { trackSource: true, sourceOffset: part.offset });
          analysisHost.appendChild(paraNode);
          const inlineFigs = figures.filter((f) => f.position === 'paragraph' && (Number.isInteger(f.paragraph) ? f.paragraph : 0) === idx);
          appendFigGroup(analysisHost, inlineFigs);
        });
        sec.appendChild(analysisHost);
      }

      appendFigGroup(sec, afterAndOverflowFigs);
      previewEl.appendChild(sec);
    }

    bindImageZoomHandlers(previewEl, `第 ${col.item?.questionNumber || '?'} 题 · 解析配图`);
  }

  /**
   * 同步左侧输入框光标/选区到右侧实时预览高亮，并柔和滚动至可视区域
   * @param {object} col 列状态对象
   * @param {HTMLTextAreaElement} textarea 当前激活输入框
   * @param {HTMLElement} previewEl 右侧实时预览容器
   * @param {HTMLElement|null} hintEl 状态提示条
   */
  function syncSolutionCursor(col, textarea, previewEl, hintEl) {
    if (!col || !textarea || !previewEl) return;
    const qb = qbApi();
    const field = textarea.dataset.field;
    if (!field) return;
    SOL.lastField = field;
    const fieldLabel = field === 'answer' ? '答案' : '解析';
    const start = Number.isInteger(textarea.selectionStart) ? textarea.selectionStart : 0;
    const end = Number.isInteger(textarea.selectionEnd) ? textarea.selectionEnd : start;

    let hit = null;
    if (qb && typeof qb.previewSelection === 'function') {
      hit = qb.previewSelection(previewEl, { field, start, end });
    }

    if (hintEl) {
      if (!String(textarea.value || '').trim()) {
        hintEl.textContent = `正在编辑「${fieldLabel}」（当前为空，输入后即时排版）`;
      } else if (hit && hit.kind === 'math') {
        hintEl.textContent = `🧮 公式高亮联动 · ${fieldLabel}（字符 ${start}${end > start ? '-' + end : ''}）`;
      } else if (hit) {
        hintEl.textContent = `📍 已定位预览光标 · ${fieldLabel}（字符 ${start}${end > start ? '-' + end : ''}）`;
      } else {
        hintEl.textContent = `正在编辑「${fieldLabel}」`;
      }
    }

    const activeNode = previewEl.querySelector('.qb-preview-active-formula, .qb-preview-active-region, .qb-preview-caret, .qb-preview-selection');
    if (activeNode && typeof activeNode.getBoundingClientRect === 'function' && typeof previewEl.getBoundingClientRect === 'function') {
      const rootRect = previewEl.getBoundingClientRect();
      const targetRect = activeNode.getBoundingClientRect();
      if (rootRect.height > 0 && (targetRect.top < rootRect.top + 8 || targetRect.bottom > rootRect.bottom - 8)) {
        previewEl.scrollTop += (targetRect.top - rootRect.top) - Math.round(previewEl.clientHeight / 3);
      }
    }
  }

  /**
   * 根据内容自适应调整 textarea 高度（避免输入框内出现过窄滚动条）
   * @param {HTMLTextAreaElement} textarea 输入框
   * @param {number} [minPx=76] 最小高度
   * @param {number} [maxPx=360] 最大高度
   */
  function fitTextarea(textarea, minPx = 76, maxPx = 360) {
    if (!textarea || !textarea.style) return;
    textarea.style.height = 'auto';
    const next = Math.max(minPx, Math.min(maxPx, Number(textarea.scrollHeight) || minPx));
    textarea.style.height = `${next}px`;
  }

  /**
   * 对全局 API 更新提示并按需刷新工作区状态文案
   * @param {string} msg 状态文案
   */
  function setStatus(msg) {
    const el = $el('solutionEditorStatus');
    if (el) el.textContent = msg || '';
  }

  /**
   * 同步顶部「双栏预览 / 多题并排」切换按钮激活样式
   */
  function syncModeButtons() {
    const splitBtn = $el('solModeSplitBtn');
    const colsBtn = $el('solModeColumnsBtn');
    if (!splitBtn || !colsBtn) return;
    const isSplit = SOL.viewMode === 'split';
    splitBtn.className = isSplit
      ? 'px-2.5 py-1 rounded-md font-medium transition-colors bg-white text-emerald-700 shadow-sm'
      : 'px-2.5 py-1 rounded-md font-medium transition-colors text-slate-500 hover:text-slate-800';
    colsBtn.className = !isSplit
      ? 'px-2.5 py-1 rounded-md font-medium transition-colors bg-white text-emerald-700 shadow-sm'
      : 'px-2.5 py-1 rounded-md font-medium transition-colors text-slate-500 hover:text-slate-800';
  }

  /**
   * 获取/修正当前聚焦题目 ID
   * @returns {string|null} 当前激活 ID
   */
  function ensureActiveId() {
    if (!SOL.order.length) {
      SOL.activeId = null;
      return null;
    }
    if (!SOL.activeId || !SOL.order.includes(SOL.activeId)) {
      SOL.activeId = SOL.order[0];
    }
    return SOL.activeId;
  }

  /**
   * 仅更新当前列的状态徽章、未保存圆点与顶部 Tab，避免打断输入焦点
   * @param {object} col 列状态对象
   */
  function refreshColumnDirtyIndicators(col) {
    if (!col) return;
    const colEl = document.querySelector(`#solutionEditorColumns [data-col="${col.id}"]`);
    if (colEl) {
      const badge = colEl.querySelector('[data-col-status]');
      if (badge) {
        const text = col.saving ? '正在保存...' : col.dirty ? '有未保存修改' : col.baseRevision ? `已保存 v${col.baseRevision}` : '未写正式解析';
        badge.textContent = text;
        badge.className = col.dirty
          ? 'text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200'
          : col.baseRevision
            ? 'text-[10px] font-medium px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200'
            : 'text-[10px] text-slate-400';
      }
    }
    renderTabs();
    renderList();
  }

  /**
   * 打开答案解析编辑器
   * @param {Array<string|number>} [initialIds] 初始选中要打开的题目 ID 列表
   */
  function openSolutionEditor(initialIds) {
    const modal = $el('solutionEditorModal');
    if (!modal) return;
    SOL.open = true;
    modal.classList.remove('hidden');
    ensureDelegation();
    const list = Array.isArray(initialIds) ? initialIds.map(String).filter(Boolean) : [];
    const targetIds = list.length ? list.slice(0, MAX_COLUMNS) : [...SOL.order];
    SOL.order = [];
    for (const id of targetIds) {
      if (findItem(id) && !SOL.order.includes(id)) SOL.order.push(id);
    }
    if (SOL.order.length) {
      SOL.activeId = SOL.order[0];
    } else {
      SOL.activeId = null;
    }
    renderList();
    syncModeButtons();
    renderWorkspace();
    SOL.order.forEach((id) => ensureColumn(id));
  }

  /**
   * 关闭答案解析编辑器并清理 AI 轮询定时器
   */
  function closeSolutionEditor() {
    SOL.open = false;
    const modal = $el('solutionEditorModal');
    if (modal) modal.classList.add('hidden');
    SOL.pollTimers.forEach((timerId) => clearTimeout(timerId));
    SOL.pollTimers.clear();
  }

  /**
   * 切换编辑器视图模式（'split' 双栏实时预览 / 'columns' 多题并排）
   * @param {'split'|'columns'} mode 目标视图模式
   */
  function setSolutionEditorMode(mode) {
    SOL.viewMode = mode === 'columns' ? 'columns' : 'split';
    syncModeButtons();
    renderWorkspace();
  }

  /**
   * 在双栏与并排模式下统一开启/聚焦某道试题
   * @param {string|number} id 试题 ID
   */
  function toggleSolutionColumn(id) {
    id = String(id);
    if (!findItem(id)) return;
    const exists = SOL.order.includes(id);
    if (SOL.viewMode === 'split') {
      if (!exists) {
        if (SOL.order.length >= MAX_COLUMNS) {
          setStatus(`最多同时打开 ${MAX_COLUMNS} 道题，请先关闭其他标签`);
          return;
        }
        SOL.order.push(id);
        ensureColumn(id);
      }
      SOL.activeId = id;
    } else {
      if (exists) {
        closeColumnById(id);
        return;
      }
      if (SOL.order.length >= MAX_COLUMNS) {
        setStatus(`最多同时打开 ${MAX_COLUMNS} 道题并排`);
        return;
      }
      SOL.order.push(id);
      SOL.activeId = id;
      ensureColumn(id);
    }
    renderList();
    renderWorkspace();
  }

  /**
   * 从工作区关闭指定题目标签/列
   * @param {string} id 试题 ID
   */
  function closeColumnById(id) {
    const idx = SOL.order.indexOf(id);
    if (idx === -1) return;
    SOL.order.splice(idx, 1);
    if (SOL.pollTimers.has(id)) {
      clearTimeout(SOL.pollTimers.get(id));
      SOL.pollTimers.delete(id);
    }
    if (SOL.activeId === id) {
      SOL.activeId = SOL.order[Math.min(idx, SOL.order.length - 1)] || null;
    }
    renderList();
    renderWorkspace();
  }

  /**
   * 确保列状态已初始化并在需要时异步拉取正式解析与 AI 初稿
   * @param {string} id 试题 ID
   * @returns {object|null} 列状态对象
   */
  function ensureColumn(id) {
    if (SOL.cols.has(id)) return SOL.cols.get(id);
    const item = findItem(id);
    if (!item) return null;
    const col = {
      id,
      item,
      loading: true,
      saving: false,
      generating: false,
      genMessage: '',
      dirty: false,
      showOrigin: false,
      showAiRaw: false,
      showCrop: false,
      baseRevision: 0,
      saved: { answer: '', analysis: '', figures: [] },
      value: { answer: '', analysis: '', figures: [] },
      origin: { answer: item.rawAnswer || '', analysis: item.rawAnalysis || '' },
      ai: { answer: '', analysis: '', model: '', stale: false, updatedAt: '' },
      error: '',
    };
    SOL.cols.set(id, col);
    loadColumn(col);
    return col;
  }

  /**
   * 从后端加载该题正式解析、原卷答案与既有 AI 初稿
   * @param {object} col 列状态对象
   */
  async function loadColumn(col) {
    col.loading = true;
    col.error = '';
    renderWorkspace();
    try {
      const res = await fetch(`${apiBase()}/api/library/${encodeURIComponent(col.id)}/solution`, {
        headers: requestHeaders(false),
        cache: 'no-store',
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      const sol = data.solution || {};
      col.baseRevision = Number(data.base_revision || 0);
      col.saved = { answer: sol.answer || '', analysis: sol.analysis || '', figures: cloneFigures(sol.figures) };
      col.value = { answer: col.saved.answer, analysis: col.saved.analysis, figures: cloneFigures(col.saved.figures) };
      col.origin = data.origin || { answer: col.item.rawAnswer || '', analysis: col.item.rawAnalysis || '' };
      if (data.ai_answer) {
        col.ai = {
          answer: data.ai_answer.answer || '',
          analysis: data.ai_answer.analysis || '',
          model: data.ai_answer.model || '',
          stale: Boolean(data.ai_answer_stale),
          updatedAt: data.ai_answer.updated_at || '',
        };
      } else if (col.item?.aiAnswer) {
        col.ai = {
          answer: col.item.aiAnswer.answer || '',
          analysis: col.item.aiAnswer.analysis || '',
          model: col.item.aiAnswer.model || '',
          stale: false,
          updatedAt: col.item.aiAnswer.updated_at || '',
        };
      }
      col.dirty = false;
      if (!col.ai.answer && !col.ai.analysis) {
        fetchAiDraft(col.id);
      }
    } catch (e) {
      col.error = e.message || '加载解析失败';
    } finally {
      col.loading = false;
      renderWorkspace();
    }
  }

  /**
   * 查询后端 AI 解题任务列表并提取已完成结果作为参考初稿（不覆盖人工输入框）
   * @param {string} id 试题 ID
   * @returns {Promise<object|null>} 最新任务对象
   */
  async function fetchAiDraft(id) {
    const col = SOL.cols.get(id);
    if (!col) return null;
    try {
      const res = await fetch(`${apiBase()}/api/library/jobs?ids=${encodeURIComponent(id)}&solution_scope=true`, {
        headers: requestHeaders(false),
        cache: 'no-store',
      });
      if (!res.ok) return null;
      const data = await res.json();
      const jobs = (data.jobs || []).filter((j) => j.kind === 'answer');
      const done = jobs.find((j) => ['done', 'completed', 'succeeded', 'success'].includes(j.status) && j.result);
      if (done) {
        col.ai = {
          answer: done.result.answer || '',
          analysis: done.result.analysis || '',
          model: [done.result.engine, done.result.model].filter(Boolean).join(' · '),
          stale: false,
          updatedAt: done.updated_at || '',
        };
        if (SOL.open) renderWorkspace();
      }
      return jobs[0] || null;
    } catch (_) {
      return null;
    }
  }

  /**
   * 直接在编辑器内触发 AI 生成本题答案与解析（遵守 solution_scope=true, executor='api' 规范）
   * @param {string} id 试题 ID
   */
  async function generateAiDraftForColumn(id) {
    const col = SOL.cols.get(id);
    if (!col || col.generating) return;
    if (SOL.pollTimers.has(id)) {
      clearTimeout(SOL.pollTimers.get(id));
      SOL.pollTimers.delete(id);
    }
    col.generating = true;
    col.genMessage = '正在提交 AI 解题任务...';
    renderWorkspace();
    try {
      const res = await fetch(`${apiBase()}/api/library/jobs`, {
        method: 'POST',
        headers: requestHeaders(true),
        body: JSON.stringify({ kind: 'answer', ids: [col.id], solution_scope: true, executor: 'api' }),
      });
      let data = {};
      try { data = await res.json(); } catch (_) { data = {}; }
      if (!res.ok) throw new Error(data.error || `提交失败（HTTP ${res.status}）`);
      col.genMessage = 'AI 正在思考解题步骤，请稍候...';
      renderWorkspace();
      pollAiGeneration(id, 0);
    } catch (e) {
      col.generating = false;
      col.genMessage = `生成失败：${e.message || '请检查模型 API 配置'}`;
      renderWorkspace();
    }
  }

  /**
   * 轮询 AI 解题任务状态并在完成后刷新对照区
   * @param {string} id 试题 ID
   * @param {number} attempt 当前轮询次数
   */
  async function pollAiGeneration(id, attempt) {
    if (!SOL.open) return;
    const col = SOL.cols.get(id);
    if (!col) return;
    const latest = await fetchAiDraft(id);
    const st = latest?.status;
    if (st && ['done', 'completed', 'succeeded', 'success'].includes(st)) {
      col.generating = false;
      col.genMessage = col.ai.answer || col.ai.analysis ? 'AI 初稿已更新，可点击下方按钮采纳' : '任务已完成，但未返回初稿内容';
      renderWorkspace();
      return;
    }
    if (st && ['failed', 'cancelled', 'timed_out'].includes(st)) {
      col.generating = false;
      col.genMessage = latest.error ? `生成失败：${latest.error}` : 'AI 生成未完成，可重试';
      renderWorkspace();
      return;
    }
    if (attempt >= AI_POLL_MAX_ATTEMPTS) {
      col.generating = false;
      col.genMessage = 'AI 任务仍在后台执行中，稍后可点「刷新初稿」查看';
      renderWorkspace();
      return;
    }
    const timerId = setTimeout(() => pollAiGeneration(id, attempt + 1), AI_POLL_INTERVAL_MS);
    SOL.pollTimers.set(id, timerId);
  }

  function solutionEditorSearch(v) { SOL.search = (v || '').trim(); renderList(); }
  function solutionEditorPickBasket() { pickIds(basketIdsList()); }
  function solutionEditorPickFiltered() { pickIds(filteredList().slice(0, MAX_COLUMNS).map((q) => q.id)); }
  function solutionEditorPickMissing() { pickIds(filteredList().filter((q) => !hasSolutionStatus(q).hasFormal).slice(0, MAX_COLUMNS).map((q) => q.id)); }

  /**
   * 批量打开最多 MAX_COLUMNS 道题目到工作区
   * @param {Array<string|number>} ids 题目 ID 集合
   */
  function pickIds(ids) {
    const list = (ids || []).map(String).filter((id) => findItem(id)).slice(0, MAX_COLUMNS);
    if (!list.length) {
      setStatus('当前没有可加入的题目');
      return;
    }
    SOL.order = [];
    list.forEach((id) => {
      if (!SOL.order.includes(id)) SOL.order.push(id);
      ensureColumn(id);
    });
    SOL.activeId = SOL.order[0];
    renderList();
    renderWorkspace();
  }

  /**
   * 渲染左侧选题列表，并对题干摘要执行 KaTeX 渲染
   */
  function renderList() {
    const box = $el('solutionEditorList');
    if (!box) return;
    const kw = SOL.search.toLowerCase();
    const items = allItems().filter((q) => {
      if (!kw) return true;
      return String(q.questionNumber || '').toLowerCase().includes(kw)
        || String(q.source || '').toLowerCase().includes(kw)
        || String(q.rawStem || '').toLowerCase().includes(kw)
        || String(q.id || '').toLowerCase().includes(kw);
    });
    const shown = items.slice(0, 100);
    if (!shown.length) {
      box.innerHTML = '<div class="text-xs text-slate-400 text-center py-8">未找到匹配题目</div>';
      return;
    }
    box.innerHTML = shown.map((q) => {
      const opened = SOL.order.includes(q.id);
      const isFocused = opened && SOL.activeId === q.id;
      const col = SOL.cols.get(q.id);
      const st = hasSolutionStatus(q);
      const badge = st.hasFormal
        ? '<span class="px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 text-[10px] font-medium">已有解析</span>'
        : st.hasOrigin
          ? '<span class="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 text-[10px] font-medium">原卷有解析</span>'
          : '<span class="px-1.5 py-0.5 rounded bg-rose-50 text-rose-700 text-[10px] font-medium">缺解析</span>';
      const dirtyDot = col?.dirty ? '<span class="inline-block w-1.5 h-1.5 rounded-full bg-amber-500" title="有未保存修改"></span>' : '';
      const borderCls = isFocused
        ? 'border-emerald-500 bg-emerald-50/70 ring-1 ring-emerald-200'
        : opened
          ? 'border-emerald-300 bg-emerald-50/30'
          : 'border-slate-200 bg-white hover:border-slate-300';
      return `
        <button type="button" data-pick="${esc(q.id)}" class="w-full text-left p-2 rounded-lg border text-xs transition-colors ${borderCls}">
          <div class="flex items-center justify-between gap-1">
            <div class="flex items-center gap-1 min-w-0">
              <span class="font-bold text-slate-700 shrink-0">#${esc(q.questionNumber || q.id)}</span>
              ${badge}
              ${dirtyDot}
            </div>
            ${isFocused ? '<span class="text-[10px] text-emerald-700 font-semibold shrink-0">当前</span>' : opened ? '<span class="text-[10px] text-emerald-600 shrink-0">已打开</span>' : ''}
          </div>
          <div class="mt-1 text-[11px] text-slate-600 line-clamp-2 leading-snug sol-list-stem">${formatInlineSummaryHtml(q.rawStem)}</div>
          <div class="mt-0.5 text-[10px] text-slate-400 truncate">${esc(q.source || '')}</div>
        </button>`;
    }).join('') + (items.length > 100 ? `<div class="text-[10px] text-slate-400 text-center py-2">仅显示前 100 道，可用上方搜索框精确定位</div>` : '');
    box.querySelectorAll('[data-pick]').forEach((btn) => {
      btn.addEventListener('click', () => toggleSolutionColumn(btn.dataset.pick));
    });
    applyKaTeXFallback(box);
  }

  /**
   * 渲染顶部已打开题目 Tab 切换带（保留未保存草稿、支持直接关闭）
   */
  function renderTabs() {
    const strip = $el('solutionEditorTabs');
    if (!strip) return;
    if (!SOL.order.length) {
      strip.classList.add('hidden');
      strip.innerHTML = '';
      return;
    }
    strip.classList.remove('hidden');
    strip.innerHTML = SOL.order.map((id, idx) => {
      const q = findItem(id);
      const col = SOL.cols.get(id);
      const isActive = SOL.viewMode === 'split' ? SOL.activeId === id : false;
      const dirty = Boolean(col?.dirty);
      const saved = Boolean(!dirty && col?.baseRevision);
      const tabCls = isActive
        ? 'border-emerald-500 bg-emerald-50 text-emerald-800 font-semibold shadow-sm'
        : 'border-slate-200 bg-slate-50/70 text-slate-600 hover:bg-slate-100';
      return `
        <div class="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border text-[11px] shrink-0 cursor-pointer transition-colors ${tabCls}" data-tab="${esc(id)}">
          <span class="font-bold">#${esc(q?.questionNumber || (idx + 1))}</span>
          <span class="max-w-[140px] truncate">${esc(q?.questionType || '试题')}${q?.source ? ' · ' + esc(q.source) : ''}</span>
          ${dirty ? '<span class="w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" title="有未保存修改"></span>' : saved ? '<span class="text-emerald-600 text-[10px] shrink-0" title="已保存">✓</span>' : ''}
          <button type="button" data-tab-close="${esc(id)}" class="ml-0.5 text-slate-400 hover:text-rose-600 px-0.5 leading-none" title="关闭此题">✕</button>
        </div>`;
    }).join('');
  }

  /**
   * 构建单道题目的编辑器左栏主体 HTML（双栏与并排模式共用）
   * @param {object} col 列状态对象
   * @param {boolean} isSplit 是否处于双栏实时预览模式
   * @returns {string} HTML 字符串
   */
  function editorControlsHtml(col, isSplit) {
    const q = col.item;
    const statusText = col.saving ? '正在保存...' : col.dirty ? '有未保存修改' : col.baseRevision ? `已保存 v${col.baseRevision}` : '未写正式解析';
    const statusCls = col.dirty
      ? 'text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200'
      : col.baseRevision
        ? 'text-[10px] font-medium px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200'
        : 'text-[10px] text-slate-400';
    return `
      <div class="flex items-center justify-between gap-2 px-3 py-2 border-b border-slate-100 bg-slate-50/70 shrink-0">
        <div class="min-w-0">
          <div class="flex items-center gap-1.5 flex-wrap">
            <span class="text-xs font-bold text-slate-900">第 ${esc(q.questionNumber || '?')} 题</span>
            <span class="text-[10px] text-slate-500 bg-white border border-slate-200 px-1.5 py-0.5 rounded">${esc(q.questionType || '试题')}</span>
            <span data-col-status class="${statusCls}">${esc(statusText)}</span>
          </div>
          <div class="text-[10px] text-slate-400 truncate mt-0.5">${esc(q.source || '')}</div>
        </div>
        <div class="flex items-center gap-1 shrink-0">
          ${q.documentId ? `<button type="button" data-act="open-paper" class="px-2 py-0.5 rounded border border-slate-200 bg-white text-[10px] text-slate-600 hover:bg-slate-100">原卷</button>` : ''}
          ${!isSplit ? '<button type="button" data-act="close" class="text-slate-400 hover:text-slate-700 text-xs px-1.5" title="移除这列">✕</button>' : ''}
        </div>
      </div>

      <div class="flex-1 min-h-0 overflow-y-auto p-3 space-y-3 text-xs">
        ${col.loading ? '<div class="text-slate-400 text-center py-8">正在从题库加载答案与解析...</div>' : `
          ${col.error ? `<div class="p-2 rounded-md bg-rose-50 text-rose-700 text-[11px] border border-rose-200">${esc(col.error)}</div>` : ''}
          ${!isSplit ? `
            <div class="rounded-lg border border-slate-200 bg-slate-50/60 p-2.5">
              <div class="text-[10px] font-semibold text-slate-500 mb-1">原题干与选项</div>
              <div data-question-preview="${esc(col.id)}" class="qb-question text-xs"></div>
            </div>` : ''}

          <div>
            <div class="flex items-center justify-between mb-1">
              <label class="font-semibold text-slate-700">答案</label>
              <span class="text-[10px] text-slate-400">支持选择题字母或 LaTeX（Ctrl+S 保存）</span>
            </div>
            <textarea data-field="answer" rows="2" placeholder="填写标准答案，多小问可用 (1) ... (2) ... 分行"
                      class="w-full px-2.5 py-1.5 rounded-md border border-slate-200 text-xs font-mono leading-relaxed focus:border-emerald-500 focus:outline-none resize-none">${esc(col.value.answer)}</textarea>
          </div>

          <div>
            <div class="flex items-center justify-between mb-1">
              <label class="font-semibold text-slate-700">解析</label>
              <span class="text-[10px] text-slate-400">支持 LaTeX ($...$) · 空行分段 · Ctrl+V 粘贴截图</span>
            </div>
            <textarea data-field="analysis" rows="5" placeholder="填写详细解题思路与步骤，公式请用 $...$ 或 $$...$$ 包裹"
                      class="w-full px-2.5 py-2 rounded-md border border-slate-200 text-xs font-mono leading-relaxed focus:border-emerald-500 focus:outline-none resize-none">${esc(col.value.analysis)}</textarea>
          </div>

          ${!isSplit ? `
            <div class="rounded-lg border border-emerald-200/80 bg-white overflow-hidden">
              <div class="px-2.5 py-1.5 bg-emerald-50/60 border-b border-emerald-100 flex items-center justify-between gap-2">
                <span class="text-[11px] font-semibold text-emerald-800">实时排版预览</span>
                <span data-preview-hint class="text-[10px] text-emerald-700 truncate">实时同步</span>
              </div>
              <div data-live-preview="${esc(col.id)}" class="relative p-2.5 max-h-60 overflow-y-auto sol-live-preview"></div>
            </div>` : ''}

          ${aiCardHtml(col)}
          ${originCardHtml(col)}
          ${figuresHtml(col)}
          ${col.showCrop ? cropHtml(col) : ''}
        `}
      </div>

      <div class="border-t border-slate-100 px-3 py-2 flex items-center justify-between gap-2 bg-slate-50/50 shrink-0">
        <div class="flex items-center gap-2">
          <button type="button" data-act="save" ${col.saving ? 'disabled' : ''}
                  class="px-3 py-1.5 rounded-md bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold disabled:opacity-50">${col.saving ? '保存中...' : '保存本题'}</button>
          <button type="button" data-act="reload" class="px-2.5 py-1.5 rounded-md border border-slate-200 bg-white text-xs text-slate-600 hover:bg-slate-100">重新读取</button>
        </div>
        <span class="text-[10px] text-slate-400">${col.baseRevision ? `版本 v${col.baseRevision}` : '草稿状态'}</span>
      </div>`;
  }

  /**
   * 构造双栏模式主视图 HTML（左 50% 编辑对照，右 50% 上题面下实时预览）
   * @param {object} col 当前激活列对象
   * @returns {string} HTML 字符串
   */
  function splitColumnHtml(col) {
    const q = col.item;
    return `
      <div data-col="${esc(col.id)}" class="grid grid-cols-1 lg:grid-cols-12 gap-3 h-full w-full min-h-0">
        <div class="lg:col-span-6 flex flex-col min-h-0 rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
          ${editorControlsHtml(col, true)}
        </div>
        <div class="lg:col-span-6 flex flex-col min-h-0 gap-3 overflow-hidden">
          <div class="flex flex-col max-h-[42%] shrink-0 rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
            <div class="flex items-center justify-between gap-2 px-3 py-2 border-b border-slate-100 bg-slate-50/70 shrink-0">
              <div class="flex items-center gap-2 min-w-0">
                <span class="text-xs font-bold text-slate-800">完整题面（含配图与选项）</span>
                <span class="text-[10px] text-slate-400">点击配图可放大</span>
              </div>
              ${q.documentId ? `<button type="button" data-act="open-paper" class="px-2 py-0.5 rounded border border-slate-200 bg-white text-[11px] text-slate-600 hover:bg-slate-100 shrink-0">对照原卷</button>` : ''}
            </div>
            <div data-question-preview="${esc(col.id)}" class="p-3.5 overflow-y-auto qb-question"></div>
          </div>
          <div class="flex-1 min-h-0 flex flex-col rounded-xl border border-emerald-200/80 bg-white shadow-sm overflow-hidden">
            <div class="flex items-center justify-between gap-2 px-3 py-2 border-b border-emerald-100 bg-emerald-50/60 shrink-0">
              <span class="text-xs font-bold text-emerald-900">实时排版预览</span>
              <span data-preview-hint class="text-[11px] text-emerald-700 truncate">点击左侧输入框可实时定位光标与公式高亮</span>
            </div>
            <div data-live-preview="${esc(col.id)}" class="relative flex-1 min-h-0 overflow-y-auto p-3.5 sol-live-preview"></div>
          </div>
        </div>
      </div>`;
  }

  /**
   * 构造多题并排模式单列 HTML（列宽随已打开题数自适应扩展）
   * @param {object} col 列状态对象
   * @param {number} total 已打开总题数
   * @returns {string} HTML 字符串
   */
  function sideColumnHtml(col, total) {
    const widthCls = total <= 1
      ? 'w-full max-w-[780px] flex-1'
      : total === 2
        ? 'min-w-[440px] flex-1 max-w-[740px]'
        : 'w-[440px] shrink-0';
    return `
      <div data-col="${esc(col.id)}" class="${widthCls} flex flex-col rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden h-full min-h-0">
        ${editorControlsHtml(col, false)}
      </div>`;
  }

  /**
   * 构造 AI 初稿对照卡片 HTML（支持公式渲染展示、源码切换、全部采纳与弹窗内一键生成）
   * @param {object} col 列状态对象
   * @returns {string} HTML 字符串
   */
  function aiCardHtml(col) {
    const hasAi = Boolean(col.ai.answer || col.ai.analysis);
    const meta = col.ai.model ? esc(col.ai.model) : (hasAi ? 'AI 初稿' : '尚未生成');
    return `
      <div class="p-2.5 rounded-lg border ${hasAi ? 'border-emerald-200 bg-emerald-50/35' : 'border-slate-200 bg-slate-50/60'} space-y-2">
        <div class="flex items-center justify-between gap-2 flex-wrap">
          <div class="flex items-center gap-1.5">
            <span class="font-semibold text-[11px] ${hasAi ? 'text-emerald-800' : 'text-slate-700'}">⚡ AI 初稿对照</span>
            <span class="text-[10px] ${hasAi ? 'text-emerald-600' : 'text-slate-400'}">${meta}${col.ai.stale ? '（旧题面版本）' : ''}</span>
          </div>
          <div class="flex items-center gap-1.5">
            ${hasAi ? `<button type="button" data-act="toggle-ai-raw" class="px-1.5 py-0.5 rounded border border-emerald-200 bg-white text-[10px] text-emerald-700 hover:bg-emerald-50">${col.showAiRaw ? '公式预览' : '查看源码'}</button>` : ''}
            <button type="button" data-act="ai-generate" ${col.generating ? 'disabled' : ''}
                    class="px-2 py-0.5 rounded bg-emerald-600 hover:bg-emerald-700 text-white text-[10px] font-medium disabled:opacity-50">
              ${col.generating ? '生成中...' : hasAi ? '重新生成' : '⚡ AI 生成本题'}
            </button>
          </div>
        </div>
        ${col.genMessage ? `<div class="text-[10px] ${col.genMessage.startsWith('生成失败') ? 'text-rose-600' : 'text-emerald-700'}">${esc(col.genMessage)}</div>` : ''}
        ${hasAi ? `
          ${col.showAiRaw ? `
            <div class="space-y-1 text-[11px] font-mono text-slate-700 bg-white/90 rounded p-2 border border-emerald-100 max-h-48 overflow-y-auto select-text">
              ${col.ai.answer ? `<div><span class="font-bold text-emerald-700">答案：</span><span class="whitespace-pre-wrap">${esc(col.ai.answer)}</span></div>` : ''}
              ${col.ai.analysis ? `<div><span class="font-bold text-emerald-700">解析：</span><span class="whitespace-pre-wrap">${esc(col.ai.analysis)}</span></div>` : ''}
            </div>` : `
            <div class="space-y-1.5 text-xs text-slate-800 bg-white/90 rounded p-2 border border-emerald-100 max-h-52 overflow-y-auto">
              ${col.ai.answer ? `<div><span class="font-semibold text-emerald-700 mr-1">答案：</span><div data-ai-render="answer" class="inline-block align-top sol-rendered-text"></div></div>` : ''}
              ${col.ai.analysis ? `<div><div class="font-semibold text-emerald-700 mb-0.5">解析：</div><div data-ai-render="analysis" class="sol-rendered-text text-slate-800"></div></div>` : ''}
            </div>`}
          <div class="flex flex-wrap gap-1.5 pt-0.5">
            ${col.ai.answer && col.ai.analysis ? '<button type="button" data-act="adopt-both" class="px-2 py-0.5 rounded bg-emerald-600 text-white text-[10px] font-medium hover:bg-emerald-700">全部采纳</button>' : ''}
            ${col.ai.answer ? '<button type="button" data-act="adopt-answer" class="px-2 py-0.5 rounded border border-emerald-200 bg-white text-emerald-700 text-[10px] hover:bg-emerald-50">采纳到答案</button>' : ''}
            ${col.ai.analysis ? '<button type="button" data-act="adopt-analysis" class="px-2 py-0.5 rounded border border-emerald-200 bg-white text-emerald-700 text-[10px] hover:bg-emerald-50">采纳到解析</button>' : ''}
          </div>` : `
          <div class="text-[11px] text-slate-400">暂无 AI 初稿，可点击右上角「⚡ AI 生成本题」生成参考步骤后一键采纳。</div>`}
      </div>`;
  }

  /**
   * 构造原卷参考答案只读折叠卡 HTML（展开后公式自动渲染）
   * @param {object} col 列状态对象
   * @returns {string} HTML 字符串
   */
  function originCardHtml(col) {
    const o = col.origin || {};
    if (!o.answer && !o.analysis) return '';
    return `
      <div class="p-2.5 rounded-lg border border-amber-200 bg-amber-50/40 space-y-1.5">
        <button type="button" data-act="toggle-origin" class="w-full flex items-center justify-between text-[11px] font-semibold text-amber-800">
          <span>📄 原卷参考答案（只读参考，未写回题库）</span>
          <span>${col.showOrigin ? '收起 ▲' : '展开 ▼'}</span>
        </button>
        ${col.showOrigin ? `
          <div class="space-y-1.5 text-xs text-slate-800 bg-white/90 rounded p-2 border border-amber-100 max-h-48 overflow-y-auto">
            ${o.answer ? `<div><span class="font-semibold text-amber-700 mr-1">答案：</span><div data-origin-render="answer" class="inline-block align-top sol-rendered-text"></div></div>` : ''}
            ${o.analysis ? `<div><div class="font-semibold text-amber-700 mb-0.5">解析：</div><div data-origin-render="analysis" class="sol-rendered-text"></div></div>` : ''}
          </div>
          <div class="flex flex-wrap gap-1.5">
            ${o.answer && o.analysis ? '<button type="button" data-act="origin-both" class="px-2 py-0.5 rounded bg-amber-600 text-white text-[10px] hover:bg-amber-700">全部填入</button>' : ''}
            ${o.answer ? '<button type="button" data-act="origin-answer" class="px-2 py-0.5 rounded border border-amber-200 bg-white text-amber-800 text-[10px] hover:bg-amber-50">填入答案</button>' : ''}
            ${o.analysis ? '<button type="button" data-act="origin-analysis" class="px-2 py-0.5 rounded border border-amber-200 bg-white text-amber-800 text-[10px] hover:bg-amber-50">填入解析</button>' : ''}
          </div>` : ''}
      </div>`;
  }

  /**
   * 构造解析配图列表与位置/尺寸编辑 HTML（含第 N 段后序号调整）
   * @param {object} col 列状态对象
   * @returns {string} HTML 字符串
   */
  function figuresHtml(col) {
    const figs = col.value.figures || [];
    return `
      <div class="p-2.5 rounded-lg border border-slate-200 bg-slate-50/40 space-y-2">
        <div class="flex items-center justify-between gap-2 flex-wrap">
          <span class="font-semibold text-[11px] text-slate-700">🖼️ 解析配图（${figs.length} 张）</span>
          <div class="flex gap-1.5">
            ${col.item.documentId ? `<button type="button" data-act="toggle-crop" class="px-2 py-0.5 rounded border border-slate-200 bg-white text-[10px] text-slate-700 hover:bg-slate-100">+ 从原卷裁图</button>` : ''}
            <label class="px-2 py-0.5 rounded border border-slate-200 bg-white text-[10px] text-slate-700 hover:bg-slate-100 cursor-pointer">
              + 上传图片<input type="file" accept="image/*" data-upload class="hidden">
            </label>
          </div>
        </div>
        ${figs.length ? figs.map((f, i) => `
          <div class="flex items-center gap-2 p-1.5 rounded-md border border-slate-200 bg-white flex-wrap">
            <img src="${esc(resolveAssetUrl(f.url))}" alt="解析配图 ${i + 1}" data-zoom-fig="${i}" class="w-14 h-12 object-contain bg-slate-50 rounded border border-slate-100 shrink-0 cursor-zoom-in">
            <div class="flex-1 min-w-[180px] space-y-1">
              <div class="flex items-center gap-1 text-[10px] text-slate-600 flex-wrap">
                <span class="font-semibold text-slate-700">图${i + 1}</span>
                <span>宽</span>
                <input type="number" min="5" max="178" value="${esc(f.display_width)}" data-fig-width="${i}" class="w-12 px-1 py-0.5 border border-slate-200 rounded text-[10px]">
                <span>mm ·</span>
                <select data-fig-pos="${i}" class="px-1 py-0.5 border border-slate-200 rounded text-[10px] bg-white">
                  <option value="before" ${f.position === 'before' ? 'selected' : ''}>解析前</option>
                  <option value="after" ${f.position === 'after' ? 'selected' : ''}>解析后</option>
                  <option value="paragraph" ${f.position === 'paragraph' ? 'selected' : ''}>指定段后</option>
                </select>
                ${f.position === 'paragraph' ? `
                  <span class="inline-flex items-center gap-0.5">第
                    <input type="number" min="1" max="50" value="${esc((Number.isInteger(f.paragraph) ? f.paragraph : 0) + 1)}" data-fig-para="${i}" class="w-10 px-1 py-0.5 border border-slate-200 rounded text-[10px]">
                    段后
                  </span>` : ''}
              </div>
            </div>
            <div class="flex items-center gap-0.5 shrink-0">
              <button type="button" data-act="fig-up" data-idx="${i}" ${i === 0 ? 'disabled' : ''} class="px-1 text-[11px] text-slate-500 hover:text-slate-800 disabled:opacity-30" title="上移">↑</button>
              <button type="button" data-act="fig-down" data-idx="${i}" ${i === figs.length - 1 ? 'disabled' : ''} class="px-1 text-[11px] text-slate-500 hover:text-slate-800 disabled:opacity-30" title="下移">↓</button>
              <button type="button" data-act="fig-remove" data-idx="${i}" class="px-1 text-[11px] text-rose-500 hover:text-rose-700" title="移除配图">✕</button>
            </div>
          </div>`).join('') : '<div class="text-[11px] text-slate-400">尚未添加解析配图，可直接 Ctrl+V 粘贴截图、上传图片或从原卷框选裁剪。</div>'}
      </div>`;
  }

  /**
   * 构造原卷画布裁剪面板 HTML
   * @param {object} col 列状态对象
   * @returns {string} HTML 字符串
   */
  function cropHtml(col) {
    const c = SOL.crops.get(col.id) || {};
    const pageOpts = (c.pages || []).map((p) => {
      const idx = p.page_index ?? p.pageIndex ?? 0;
      return `<option value="${idx}" ${idx === c.pageIdx ? 'selected' : ''}>第 ${idx + 1} 页</option>`;
    }).join('');
    const src = c.loading ? '' : `${apiBase()}/api/documents/${encodeURIComponent(col.item.documentId)}/pages/${c.pageIdx}/preview`;
    return `
      <div class="p-2.5 rounded-lg border border-sky-200 bg-sky-50/40 space-y-2" data-crop="${esc(col.id)}">
        <div class="flex items-center justify-between gap-2">
          <span class="font-semibold text-[11px] text-sky-900">原卷页裁图（在图上拖动框选区域）</span>
          <select data-crop-page class="px-1.5 py-0.5 border border-sky-200 rounded bg-white text-[11px]">${pageOpts}</select>
        </div>
        <div class="relative border border-slate-300 bg-white rounded overflow-auto max-h-96 select-none" data-crop-surface style="cursor:crosshair;">
          ${c.loading ? '<div class="p-6 text-center text-slate-400 text-[11px]">正在加载原卷页面...</div>' : `
            <img src="${esc(src)}" alt="原卷页面" draggable="false" class="w-full h-auto block select-none">
            <div data-crop-box class="${c.box ? '' : 'hidden'} absolute border-2 border-emerald-500 bg-emerald-500/15 pointer-events-none"
                 style="${c.box ? `left:${c.box[0] * 100}%;top:${c.box[1] * 100}%;width:${(c.box[2] - c.box[0]) * 100}%;height:${(c.box[3] - c.box[1]) * 100}%` : ''}"></div>`}
        </div>
        <div class="flex items-center justify-end gap-2">
          <button type="button" data-act="crop-cancel" class="px-2 py-0.5 rounded border border-slate-200 bg-white text-[11px] text-slate-600">取消</button>
          <button type="button" data-act="crop-save" ${!c.box || c.busy ? 'disabled' : ''} class="px-2.5 py-0.5 rounded bg-sky-600 hover:bg-sky-700 text-white text-[11px] disabled:opacity-50">${c.busy ? '裁图中...' : '保存裁剪图'}</button>
        </div>
      </div>`;
  }

  /**
   * 填充已挂载列的公式题面、AI/原卷对照渲染及右侧实时排版预览
   * @param {HTMLElement} rootEl 主工作区容器
   */
  function hydrateWorkspaceDom(rootEl) {
    if (!rootEl) return;
    rootEl.querySelectorAll('[data-col]').forEach((colEl) => {
      const colId = colEl.dataset.col;
      const col = SOL.cols.get(colId);
      if (!col) return;

      const stemBox = colEl.querySelector(`[data-question-preview="${colId}"]`);
      if (stemBox) renderQuestionStemPreview(stemBox, col.item);

      const aiAns = colEl.querySelector('[data-ai-render="answer"]');
      if (aiAns && col.ai.answer) renderFormulaTextNode(aiAns, col.ai.answer);
      const aiAna = colEl.querySelector('[data-ai-render="analysis"]');
      if (aiAna && col.ai.analysis) renderFormulaTextNode(aiAna, col.ai.analysis);

      const orgAns = colEl.querySelector('[data-origin-render="answer"]');
      if (orgAns && col.origin.answer) renderFormulaTextNode(orgAns, col.origin.answer);
      const orgAna = colEl.querySelector('[data-origin-render="analysis"]');
      if (orgAna && col.origin.analysis) renderFormulaTextNode(orgAna, col.origin.analysis);

      const ansTa = colEl.querySelector('textarea[data-field="answer"]');
      const anaTa = colEl.querySelector('textarea[data-field="analysis"]');
      if (ansTa) {
        ansTa.value = col.value.answer || '';
        fitTextarea(ansTa, 64, 220);
      }
      if (anaTa) {
        anaTa.value = col.value.analysis || '';
        fitTextarea(anaTa, 140, 380);
      }

      const liveEl = colEl.querySelector(`[data-live-preview="${colId}"]`);
      const hintEl = colEl.querySelector('[data-preview-hint]');
      if (liveEl && !col.loading) {
        renderLiveSolutionPreview(col, liveEl, hintEl);
      }

      colEl.querySelectorAll('[data-zoom-fig]').forEach((img) => {
        img.addEventListener('click', (e) => {
          e.stopPropagation();
          const src = img.currentSrc || img.src;
          if (src && typeof window.previewImageModal === 'function') {
            window.previewImageModal(src, `第 ${col.item?.questionNumber || '?'} 题 · 解析配图`);
          }
        });
      });

      const surface = colEl.querySelector('[data-crop-surface]');
      if (surface) bindCropSurface(colId, surface);
    });
  }

  /**
   * 渲染主工作区（根据 SOL.viewMode 展示双栏预览或并排多列，以及顶部多题 Tab 带）
   */
  function renderWorkspace() {
    const box = $el('solutionEditorColumns');
    if (!box) return;
    ensureActiveId();
    renderTabs();
    syncModeButtons();

    if (!SOL.order.length) {
      box.className = 'flex-1 min-h-0 overflow-hidden p-3 flex gap-3';
      box.innerHTML = '<div class="m-auto text-center text-xs text-slate-400 space-y-2 py-12"><div class="text-sm font-semibold text-slate-600">请在左侧选择要编辑解析的题目</div><div>支持同时加入多道题目通过顶部标签快速切换，也可切换为「多题并排」对比编辑。</div></div>';
      setStatus('');
      return;
    }

    if (SOL.viewMode === 'split') {
      box.className = 'flex-1 min-h-0 overflow-hidden p-3 flex';
      const activeCol = SOL.cols.get(SOL.activeId) || ensureColumn(SOL.activeId);
      box.innerHTML = activeCol ? splitColumnHtml(activeCol) : '';
    } else {
      box.className = 'flex-1 min-h-0 overflow-x-auto overflow-y-hidden p-3 flex gap-3';
      const cols = SOL.order.map((id) => ensureColumn(id)).filter(Boolean);
      box.innerHTML = cols.map((col) => sideColumnHtml(col, cols.length)).join('');
    }

    hydrateWorkspaceDom(box);
    const dirtyCount = SOL.order.filter((id) => SOL.cols.get(id)?.dirty).length;
    setStatus(`已打开 ${SOL.order.length} 道题${dirtyCount ? ` · ${dirtyCount} 题未保存` : ''}`);
  }

  /**
   * 绑定一次性事件委托与全局快捷键（Ctrl+S 保存当前题、Ctrl+V 粘贴上传图片）
   */
  function ensureDelegation() {
    const colsBox = $el('solutionEditorColumns');
    const tabsBox = $el('solutionEditorTabs');
    if (tabsBox && !tabsBox.dataset.bound) {
      tabsBox.dataset.bound = '1';
      tabsBox.addEventListener('click', (e) => {
        const closeBtn = e.target.closest('[data-tab-close]');
        if (closeBtn) {
          e.stopPropagation();
          closeColumnById(closeBtn.dataset.tabClose);
          return;
        }
        const tabEl = e.target.closest('[data-tab]');
        if (tabEl) {
          SOL.activeId = tabEl.dataset.tab;
          if (SOL.viewMode !== 'split') SOL.viewMode = 'split';
          renderList();
          renderWorkspace();
        }
      });
    }

    if (colsBox && !SOL.delegationBound) {
      SOL.delegationBound = true;
      colsBox.addEventListener('click', onColumnAction);
      colsBox.addEventListener('input', onColumnInput);
      colsBox.addEventListener('change', onColumnChange);
      ['click', 'keyup', 'select', 'focusin'].forEach((evtName) => {
        colsBox.addEventListener(evtName, (e) => {
          const ta = e.target.closest('textarea[data-field]');
          if (!ta) return;
          const colEl = ta.closest('[data-col]');
          const col = colEl && SOL.cols.get(colEl.dataset.col);
          if (!col) return;
          const previewEl = colEl.querySelector(`[data-live-preview="${col.id}"]`);
          const hintEl = colEl.querySelector('[data-preview-hint]');
          syncSolutionCursor(col, ta, previewEl, hintEl);
        });
      });
    }

    if (!SOL.shortcutsBound) {
      SOL.shortcutsBound = true;
      document.addEventListener('keydown', (e) => {
        if (!SOL.open) return;
        const modal = $el('solutionEditorModal');
        if (!modal || modal.classList.contains('hidden')) return;
        if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && (e.key === 's' || e.key === 'S')) {
          e.preventDefault();
          e.stopPropagation();
          const focusedColEl = document.activeElement?.closest?.('[data-col]');
          const targetId = focusedColEl?.dataset.col || ensureActiveId();
          if (targetId) saveColumn(targetId);
        }
      });

      document.addEventListener('paste', (e) => {
        if (!SOL.open) return;
        const modal = $el('solutionEditorModal');
        if (!modal || modal.classList.contains('hidden')) return;
        if (!modal.contains(e.target) && !modal.contains(document.activeElement)) return;
        const items = Array.from(e.clipboardData?.items || []);
        const imageItem = items.find((it) => it.kind === 'file' && String(it.type || '').startsWith('image/'));
        if (!imageItem) return;
        const file = imageItem.getAsFile();
        if (!file) return;
        const colEl = e.target?.closest?.('[data-col]') || document.activeElement?.closest?.('[data-col]');
        const targetId = colEl?.dataset.col || ensureActiveId();
        if (!targetId) return;
        e.preventDefault();
        setStatus('正在上传剪贴板截图...');
        uploadImage(targetId, file);
      });
    }
  }

  /**
   * 处理输入事件：局部更新模型、输入框高度与实时预览（不重建输入框 DOM 以保持焦点与光标）
   * @param {Event} e 输入事件
   */
  function onColumnInput(e) {
    const colEl = e.target.closest('[data-col]');
    const col = colEl && SOL.cols.get(colEl.dataset.col);
    if (!col) return;
    const f = e.target.dataset.field;
    if (f) {
      col.value[f] = e.target.value;
      col.dirty = true;
      col.error = '';
      fitTextarea(e.target, f === 'answer' ? 64 : 140, f === 'answer' ? 220 : 380);
      const previewEl = colEl.querySelector(`[data-live-preview="${col.id}"]`);
      const hintEl = colEl.querySelector('[data-preview-hint]');
      renderLiveSolutionPreview(col, previewEl, hintEl);
      syncSolutionCursor(col, e.target, previewEl, hintEl);
      refreshColumnDirtyIndicators(col);
      return;
    }
    if (e.target.dataset.figWidth !== undefined) {
      const fig = col.value.figures[Number(e.target.dataset.figWidth)];
      if (fig) {
        fig.display_width = Math.max(5, Math.min(178, Number(e.target.value) || 54));
        col.dirty = true;
        const previewEl = colEl.querySelector(`[data-live-preview="${col.id}"]`);
        renderLiveSolutionPreview(col, previewEl, colEl.querySelector('[data-preview-hint]'));
        refreshColumnDirtyIndicators(col);
      }
      return;
    }
    if (e.target.dataset.figPara !== undefined) {
      const fig = col.value.figures[Number(e.target.dataset.figPara)];
      if (fig) {
        const oneBased = Math.max(1, parseInt(e.target.value, 10) || 1);
        fig.paragraph = oneBased - 1;
        col.dirty = true;
        const previewEl = colEl.querySelector(`[data-live-preview="${col.id}"]`);
        renderLiveSolutionPreview(col, previewEl, colEl.querySelector('[data-preview-hint]'));
        refreshColumnDirtyIndicators(col);
      }
    }
  }

  /**
   * 处理选择框或文件上传 change 事件
   * @param {Event} e change 事件
   */
  function onColumnChange(e) {
    const colEl = e.target.closest('[data-col]');
    const col = colEl && SOL.cols.get(colEl.dataset.col);
    if (!col) return;
    if (e.target.dataset.figPos !== undefined) {
      const fig = col.value.figures[Number(e.target.dataset.figPos)];
      if (fig) {
        fig.position = e.target.value;
        if (fig.position === 'paragraph' && !Number.isInteger(fig.paragraph)) {
          fig.paragraph = 0;
        }
        col.dirty = true;
        renderWorkspace();
      }
    }
    if (e.target.matches('[data-upload]') && e.target.files?.[0]) {
      uploadImage(col.id, e.target.files[0]);
    }
    if (e.target.matches('[data-crop-page]')) {
      const c = SOL.crops.get(col.id);
      if (c) {
        c.pageIdx = Number(e.target.value);
        c.box = null;
        renderWorkspace();
      }
    }
  }

  /**
   * 处理列内操作按钮点击事件
   * @param {Event} e 点击事件
   */
  function onColumnAction(e) {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const colEl = btn.closest('[data-col]');
    const col = colEl && SOL.cols.get(colEl.dataset.col);
    if (!col) return;
    const act = btn.dataset.act;
    const idx = Number(btn.dataset.idx);
    const figs = col.value.figures;

    if (act === 'close') closeColumnById(col.id);
    else if (act === 'open-paper' && col.item.documentId && typeof window.openOriginalPaperModal === 'function') {
      window.openOriginalPaperModal(col.item.documentId, col.item.sourcePageIdx || 0, `第 ${col.item.questionNumber || ''} 题 · ${col.item.source || ''}`, col.id);
    }
    else if (act === 'save') saveColumn(col.id);
    else if (act === 'reload') loadColumn(col);
    else if (act === 'toggle-origin') { col.showOrigin = !col.showOrigin; renderWorkspace(); }
    else if (act === 'toggle-ai-raw') { col.showAiRaw = !col.showAiRaw; renderWorkspace(); }
    else if (act === 'ai-generate') generateAiDraftForColumn(col.id);
    else if (act === 'adopt-both') {
      if (col.ai.answer) col.value.answer = col.ai.answer;
      if (col.ai.analysis) col.value.analysis = col.ai.analysis;
      col.dirty = true;
      renderWorkspace();
    }
    else if (act === 'adopt-answer') { col.value.answer = col.ai.answer || ''; col.dirty = true; renderWorkspace(); }
    else if (act === 'adopt-analysis') { col.value.analysis = col.ai.analysis || ''; col.dirty = true; renderWorkspace(); }
    else if (act === 'origin-both') {
      if (col.origin.answer) col.value.answer = col.origin.answer;
      if (col.origin.analysis) col.value.analysis = col.origin.analysis;
      col.dirty = true;
      renderWorkspace();
    }
    else if (act === 'origin-answer') { col.value.answer = col.origin.answer || ''; col.dirty = true; renderWorkspace(); }
    else if (act === 'origin-analysis') { col.value.analysis = col.origin.analysis || ''; col.dirty = true; renderWorkspace(); }
    else if (act === 'fig-up' && idx > 0) { [figs[idx - 1], figs[idx]] = [figs[idx], figs[idx - 1]]; col.dirty = true; renderWorkspace(); }
    else if (act === 'fig-down' && idx < figs.length - 1) { [figs[idx], figs[idx + 1]] = [figs[idx + 1], figs[idx]]; col.dirty = true; renderWorkspace(); }
    else if (act === 'fig-remove') { figs.splice(idx, 1); col.dirty = true; renderWorkspace(); }
    else if (act === 'toggle-crop') { col.showCrop = !col.showCrop; if (col.showCrop) openCrop(col); else renderWorkspace(); }
    else if (act === 'crop-cancel') { col.showCrop = false; renderWorkspace(); }
    else if (act === 'crop-save') saveCrop(col.id);
  }

  /**
   * 打开并初始化原卷裁剪面板，加载卷面页数目录
   * @param {object} col 列状态对象
   */
  async function openCrop(col) {
    const c = { loading: true, pages: [], pageIdx: Number(col.item.sourcePageIdx || 0), box: null, busy: false };
    SOL.crops.set(col.id, c);
    renderWorkspace();
    try {
      const res = await fetch(`${apiBase()}/api/papers/${encodeURIComponent(col.item.documentId)}`, { headers: requestHeaders(false) });
      const data = await res.json();
      c.pages = (data.paper?.pages || data.pages || []).slice().sort((a, b) => (a.page_index ?? 0) - (b.page_index ?? 0));
      if (!c.pages.length) c.pages = [{ page_index: c.pageIdx }];
      if (!c.pages.some((p) => (p.page_index ?? 0) === c.pageIdx)) c.pageIdx = c.pages[0].page_index ?? 0;
    } catch (_) {
      c.pages = [{ page_index: c.pageIdx }];
    } finally {
      c.loading = false;
      renderWorkspace();
    }
  }

  /**
   * 绑定原卷裁图画布的指针拉框交互
   * @param {string} id 试题 ID
   * @param {HTMLElement} surface 裁图容器
   */
  function bindCropSurface(id, surface) {
    const img = surface.querySelector('img');
    const box = surface.querySelector('[data-crop-box]');
    if (!img || !box || surface.dataset.bound) return;
    surface.dataset.bound = '1';
    let start = null;
    const pct = (e) => {
      const r = img.getBoundingClientRect();
      return [Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), Math.max(0, Math.min(1, (e.clientY - r.top) / r.height))];
    };
    const draw = (b) => {
      box.classList.remove('hidden');
      box.style.left = `${b[0] * 100}%`;
      box.style.top = `${b[1] * 100}%`;
      box.style.width = `${(b[2] - b[0]) * 100}%`;
      box.style.height = `${(b[3] - b[1]) * 100}%`;
    };
    surface.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) return;
      start = pct(e);
      surface.setPointerCapture?.(e.pointerId);
    });
    surface.addEventListener('pointermove', (e) => {
      if (!start) return;
      const p = pct(e);
      draw([Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.max(start[0], p[0]), Math.max(start[1], p[1])]);
    });
    surface.addEventListener('pointerup', (e) => {
      if (!start) return;
      const p = pct(e);
      const b = [Math.min(start[0], p[0]), Math.min(start[1], p[1]), Math.max(start[0], p[0]), Math.max(start[1], p[1])].map((v) => Number(v.toFixed(4)));
      start = null;
      const c = SOL.crops.get(id);
      if (c && b[2] - b[0] >= 0.02 && b[3] - b[1] >= 0.02) {
        c.box = b;
        renderWorkspace();
      } else {
        box.classList.add('hidden');
      }
    });
  }

  /**
   * 提交原卷框选裁剪请求并将新图添加到解析配图
   * @param {string} id 试题 ID
   */
  async function saveCrop(id) {
    const col = SOL.cols.get(id);
    const c = SOL.crops.get(id);
    if (!col || !c?.box || c.busy) return;
    c.busy = true;
    col.error = '';
    renderWorkspace();
    try {
      const res = await fetch(`${apiBase()}/api/library/${encodeURIComponent(id)}/solution-images`, {
        method: 'POST',
        headers: requestHeaders(true),
        body: JSON.stringify({ page_idx: c.pageIdx, bbox: c.box }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      col.value.figures.push({
        id: data.figure.id,
        url: data.figure.url,
        display_width: data.figure.display_width || 54,
        position: data.figure.position || 'after',
        paragraph: Number.isInteger(data.figure.paragraph) ? data.figure.paragraph : null,
      });
      col.dirty = true;
      col.showCrop = false;
    } catch (e) {
      col.error = e.message || '裁图失败';
    } finally {
      c.busy = false;
      renderWorkspace();
    }
  }

  /**
   * 上传本地图片或剪贴板图片文件为解析配图
   * @param {string} id 试题 ID
   * @param {File} file 图片文件对象
   */
  async function uploadImage(id, file) {
    const col = SOL.cols.get(id);
    if (!col) return;
    col.error = '';
    try {
      const fd = new FormData();
      fd.append('image', file);
      const res = await fetch(`${apiBase()}/api/library/${encodeURIComponent(id)}/solution-images`, {
        method: 'POST',
        headers: requestHeaders(false),
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      col.value.figures.push({
        id: data.figure.id,
        url: data.figure.url,
        display_width: data.figure.display_width || 54,
        position: data.figure.position || 'after',
        paragraph: Number.isInteger(data.figure.paragraph) ? data.figure.paragraph : null,
      });
      col.dirty = true;
      setStatus(`已添加配图到第 ${col.item?.questionNumber || id} 题`);
    } catch (e) {
      col.error = e.message || '上传配图失败';
    }
    renderWorkspace();
  }

  /**
   * 保存单道试题答案与解析至题库后端
   * @param {string} id 试题 ID
   */
  async function saveColumn(id) {
    const col = SOL.cols.get(id);
    if (!col || col.saving) return;
    col.saving = true;
    col.error = '';
    renderWorkspace();
    try {
      const payload = {
        answer: col.value.answer,
        analysis: col.value.analysis,
        figures: col.value.figures.map((f) => ({
          id: f.id,
          display_width: Number(f.display_width) || 54,
          position: f.position || 'after',
          paragraph: f.position === 'paragraph' ? (Number.isInteger(f.paragraph) ? f.paragraph : 0) : null,
        })),
        base_revision: col.baseRevision,
        sync_library: true,
      };
      const res = await fetch(`${apiBase()}/api/library/${encodeURIComponent(id)}/solution`, {
        method: 'POST',
        headers: requestHeaders(true),
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      const sol = data.solution || {};
      col.baseRevision = Number(data.base_revision || (col.baseRevision + 1));
      col.saved = { answer: sol.answer ?? col.value.answer, analysis: sol.analysis ?? col.value.analysis, figures: cloneFigures(sol.figures || col.value.figures) };
      col.value = { answer: col.saved.answer, analysis: col.saved.analysis, figures: cloneFigures(col.saved.figures) };
      col.dirty = false;
      col.item.savedSolution = { ...col.saved };
      if (typeof window.applyFilters === 'function') window.applyFilters();
      setStatus(`已保存第 ${col.item?.questionNumber || id} 题（v${col.baseRevision}）`);
    } catch (e) {
      col.error = e.message || '保存失败';
    } finally {
      col.saving = false;
      renderList();
      renderWorkspace();
    }
  }

  /**
   * 保存当前所有存在未保存修改的试题
   */
  async function saveAllSolutionColumns() {
    const targets = SOL.order.filter((id) => SOL.cols.get(id)?.dirty);
    if (!targets.length) {
      setStatus('当前没有待保存的修改');
      return;
    }
    setStatus(`正在保存 ${targets.length} 道题...`);
    for (const id of targets) await saveColumn(id);
    const failed = targets.filter((id) => SOL.cols.get(id)?.error).length;
    setStatus(failed ? `保存完成，其中 ${failed} 题存在异常` : `已保存全部 ${targets.length} 道题`);
  }

  window.openSolutionEditor = openSolutionEditor;
  window.closeSolutionEditor = closeSolutionEditor;
  window.saveAllSolutionColumns = saveAllSolutionColumns;
  window.toggleSolutionColumn = toggleSolutionColumn;
  window.setSolutionEditorMode = setSolutionEditorMode;
  window.solutionEditorSearch = solutionEditorSearch;
  window.solutionEditorPickBasket = solutionEditorPickBasket;
  window.solutionEditorPickFiltered = solutionEditorPickFiltered;
  window.solutionEditorPickMissing = solutionEditorPickMissing;
})();
