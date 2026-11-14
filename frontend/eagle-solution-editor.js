/* B5：答案解析编辑器 —— 多题并排 / 原卷裁图 / AI 初稿对照
 *
 * 只用题有据的正式接口，不造任何假数据：
 *   GET  /api/library/{id}/solution            取当前正式解析 + 原卷答案 + AI 初稿
 *   POST /api/library/{id}/solution            保存正式解析（sync_library 写回题库）
 *   POST /api/library/{id}/solution-images     原卷裁图 / 上传图，返回 figure
 *   GET  /api/papers/{document_id}             原卷页列表
 *   GET  /api/documents/{document_id}/pages/{page_idx}/preview   原卷页图
 *
 * 设计原则（用户硬规则）：
 *   - 只做单题编辑、点一下采纳，不做批量落库；
 *   - 不做撤回/撤出；
 *   - 多题「并排」只是多个编辑列同时打开，各自独立保存。
 */
(function () {
  'use strict';

  const SOL = {
    open: false,
    order: [],      // 并排的题目 id，顺序 = 并排顺序
    cols: new Map(), // id -> { id, item, value, base, ai, stale, status, dirty, crop }
    keyword: ''
  };

  const esc = (value) => {
    if (typeof window.escapeHtml === 'function') return window.escapeHtml(value);
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  };

  const apiHeaders = { 'Content-Type': 'application/json', 'X-QB-Request': '1' };

  function allQuestions() {
    const list = (typeof filteredQuestions !== 'undefined' && filteredQuestions && filteredQuestions.length)
      ? filteredQuestions
      : (typeof questionsData !== 'undefined' ? questionsData : []);
    return list || [];
  }

  function findItem(id) {
    const pool = (typeof questionsData !== 'undefined' ? questionsData : []) || [];
    return pool.find(q => String(q.id) === String(id)) || allQuestions().find(q => String(q.id) === String(id)) || null;
  }

  function candidates() {
    const list = allQuestions();
    const kw = String(SOL.keyword || '').trim().toLowerCase();
    if (!kw) return list.slice(0, 300);
    return list.filter(q => {
      const hay = `${q.questionNumber || ''} ${q.source || ''} ${q.questionType || ''} ${q.rawStem || ''} ${String(q.content || '').replace(/<[^>]+>/g, ' ')}`.toLowerCase();
      return hay.includes(kw);
    }).slice(0, 300);
  }

  function normalizeFigures(list) {
    return (Array.isArray(list) ? list : []).map(f => ({
      id: f.id,
      url: f.url || '',
      display_width: Math.round(Math.max(5, Math.min(178, Number(f.display_width) || 20)) * 100) / 100,
      position: ['before', 'after', 'paragraph'].includes(f.position) ? f.position : 'after',
      paragraph: Math.max(0, Math.min(1000, Math.floor(Number(f.paragraph) || 0)))
    }));
  }

  /* ---------------- 打开 / 关闭 ---------------- */

  function openSolutionEditor(ids) {
    const modal = document.getElementById('solutionEditorModal');
    if (!modal) return;
    modal.classList.remove('hidden');
    SOL.open = true;

    let wanted = Array.isArray(ids) ? ids.map(String) : [];
    if (!wanted.length) {
      const basket = (typeof basketQuestionIds !== 'undefined' ? Array.from(basketQuestionIds) : []).map(String);
      wanted = basket.length ? basket : allQuestions().slice(0, 3).map(q => String(q.id));
    }
    wanted = wanted.slice(0, 6);
    SOL.order = wanted;
    wanted.forEach(id => loadColumn(id));

    renderColumns();
    try { renderList(); } catch (error) { console.warn('答案解析编辑器列表渲染失败', error); }
    setStatus(SOL.order.length ? `已打开 ${SOL.order.length} 道题（最多 6 道并排）` : '从左侧选题开始');
  }

  function closeSolutionEditor() {
    const modal = document.getElementById('solutionEditorModal');
    if (modal) modal.classList.add('hidden');
    SOL.open = false;
  }

  function setStatus(text, tone) {
    const el = document.getElementById('solutionEditorStatus');
    if (!el) return;
    el.innerText = text || '';
    el.className = 'text-[11px] ' + (tone === 'error' ? 'text-rose-600' : tone === 'ok' ? 'text-emerald-700' : 'text-slate-500');
  }

  /* ---------------- 左侧题目列表 ---------------- */

  function renderList() {
    const box = document.getElementById('solutionEditorList');
    if (!box) return;
    const rows = candidates();
    if (!rows.length) {
      box.innerHTML = '<div class="p-3 text-[11px] text-slate-400">当前筛选下没有题，换个关键词或先在题库页筛选。</div>';
      return;
    }
    box.innerHTML = rows.map(q => {
      const active = SOL.order.includes(String(q.id));
      const state = q.savedSolution ? '已有解析' : (q.rawAnalysis || q.rawAnswer ? '原卷有解析' : '缺解析');
      const tone = q.savedSolution ? 'text-emerald-700 bg-emerald-50' : (q.rawAnalysis || q.rawAnswer ? 'text-slate-500 bg-slate-100' : 'text-amber-700 bg-amber-50');
      return `
        <button data-qid="${esc(q.id)}" class="w-full text-left px-2 py-1.5 rounded-md border transition-colors ${active ? 'border-emerald-400 bg-emerald-50/70' : 'border-transparent hover:bg-slate-100'}">
          <div class="flex items-center gap-1.5">
            <span class="text-[10px] font-mono text-slate-500">#${esc(q.questionNumber)}</span>
            <span class="text-[10px] px-1 py-0.5 rounded ${tone}">${state}</span>
            ${active ? '<span class="text-[10px] text-emerald-700 font-semibold ml-auto">并排中</span>' : ''}
          </div>
          <div class="text-[11px] text-slate-600 truncate mt-0.5">${esc(String(q.rawStem || q.content || '').replace(/\s+/g, ' ').slice(0, 60))}</div>
        </button>`;
    }).join('');
    box.querySelectorAll('button[data-qid]').forEach(btn => {
      btn.addEventListener('click', () => toggleColumn(btn.dataset.qid));
    });
  }

  function toggleColumn(id) {
    id = String(id);
    const idx = SOL.order.indexOf(id);
    if (idx >= 0) {
      SOL.order.splice(idx, 1);
      SOL.cols.delete(id);
    } else {
      if (SOL.order.length >= 6) {
        setStatus('最多并排 6 道题，先关掉一列再加', 'error');
        return;
      }
      SOL.order.push(id);
      loadColumn(id);
    }
    renderList();
    renderColumns();
    setStatus(SOL.order.length ? `已打开 ${SOL.order.length} 道题` : '全部关闭');
  }

  /* ---------------- 数据读取 ---------------- */

  async function loadColumn(id) {
    const col = {
      id: String(id),
      item: findItem(id),
      value: { answer: '', analysis: '', figures: [] },
      base: null,
      ai: null,
      stale: false,
      status: '正在读取…',
      dirty: false,
      crop: { pages: [], page: null, range: null, busy: false }
    };
    SOL.cols.set(col.id, col);
    renderColumns();
    try {
      const res = await fetch(`${window.API_BASE || ''}/api/library/${encodeURIComponent(col.id)}/solution`, { headers: { 'X-QB-Request': '1' } });
      if (!res.ok) throw new Error(`读取解析失败（HTTP ${res.status}）`);
      const body = await res.json();
      const solution = body.solution || null;
      const origin = body.origin || {};
      col.ai = body.ai_answer || null;
      col.stale = Boolean(body.ai_answer_stale);
      if (!col.ai) {
        const draft = await fetchAiDraft(col.id);
        if (draft) {
          col.ai = draft;
          col.stale = false;
        }
      }
      col.base = body.base_revision || null;
      col.origin = { answer: String(origin.answer || ''), analysis: String(origin.analysis || '') };
      col.value = {
        answer: String((solution && solution.answer) || origin.answer || ''),
        analysis: String((solution && solution.analysis) || origin.analysis || ''),
        figures: normalizeFigures(solution ? solution.figures : [])
      };
      col.status = solution ? '已有正式解析，可继续编辑' : '尚无正式解析，当前显示原卷答案';
    } catch (error) {
      col.status = (error && error.message) || '读取失败';
    }
    renderColumns();
  }

  /* AI 初稿不只在 solution.ai_answer 里。
   * 后端在 solution_scope=true 时把 AI 结果只写进 job.result，
   * /api/library/{id}/solution 的 ai_answer 字段会是 null ——
   * 所以这里额外把后台任务的 result 捞回来当初稿。 */
  async function fetchAiDraft(publicationId) {
    try {
      const res = await fetch(
        `${window.API_BASE || ''}/api/library/jobs?ids=${encodeURIComponent(publicationId)}&solution_scope=true`,
        { headers: { 'X-QB-Request': '1' }, cache: 'no-store' }
      );
      if (!res.ok) return null;
      const body = await res.json();
      const jobs = Array.isArray(body.jobs) ? body.jobs : [];
      const done = jobs
        .filter((j) => j && j.kind === 'answer' && j.status === 'done' && j.result)
        .sort((a, b) => String(b.updated_at || b.id || '').localeCompare(String(a.updated_at || a.id || '')));
      if (!done.length) return null;
      const r = done[0].result || {};
      const answer = typeof r.answer === 'string' ? r.answer : '';
      const analysis = typeof r.analysis === 'string' ? r.analysis : '';
      if (!answer && !analysis) return null;
      return { answer, analysis, engine: r.engine || r.model || 'AI' };
    } catch (e) {
      return null;
    }
  }

  /* ---------------- 渲染并排列 ---------------- */

  function columnHtml(col) {
    const item = col.item || {};
    const ai = col.ai;
    const aiBox = ai && (ai.answer || ai.analysis)
      ? `<div class="rounded-md border border-emerald-200 bg-emerald-50/60 p-2 space-y-1.5">
           <div class="flex items-center justify-between">
             <span class="text-[10px] font-bold text-emerald-900">⚡ AI 初稿对照${col.stale ? '（已过期，不可直接采纳）' : ''}</span>
             <span class="text-[10px] text-emerald-700/80">${esc(ai.engine || 'AI')}</span>
           </div>
           ${ai.answer ? `<div class="text-[11px] text-emerald-950"><b>答案：</b>${esc(ai.answer)}</div>` : ''}
           ${ai.analysis ? `<div class="text-[11px] text-emerald-950 whitespace-pre-wrap"><b>解析：</b>${esc(ai.analysis)}</div>` : ''}
           <div class="flex gap-1.5 pt-0.5">
             <button data-act="adopt-answer" ${col.stale ? 'disabled' : ''} class="px-1.5 py-0.5 rounded border border-emerald-300 text-[10px] text-emerald-800 ${col.stale ? 'opacity-40 cursor-not-allowed' : 'hover:bg-emerald-100'}">采纳到答案</button>
             <button data-act="adopt-analysis" ${col.stale ? 'disabled' : ''} class="px-1.5 py-0.5 rounded border border-emerald-300 text-[10px] text-emerald-800 ${col.stale ? 'opacity-40 cursor-not-allowed' : 'hover:bg-emerald-100'}">采纳到解析</button>
           </div>
         </div>`
      : `<div class="rounded-md border border-dashed border-slate-200 bg-slate-50 p-2 text-[10px] text-slate-400">这道题还没有 AI 初稿。用顶部「⚡ AI 一键生成推导与解析」排一个任务，初稿会回到这里。</div>`;

    const origin = col.origin || {};
    const originBox = (origin.answer || origin.analysis)
      ? `<details class="rounded-md border border-slate-200 bg-white">
           <summary class="px-2 py-1 text-[10px] font-bold text-slate-600 cursor-pointer select-none">原卷答案（只读参考）</summary>
           <div class="px-2 pb-2 space-y-1 text-[11px] text-slate-600">
             ${origin.answer ? `<div><b>答案：</b>${esc(origin.answer)}</div>` : ''}
             ${origin.analysis ? `<div class="whitespace-pre-wrap"><b>解析：</b>${esc(origin.analysis)}</div>` : ''}
             <div class="flex gap-1.5 pt-1">
               <button data-act="origin-answer" class="px-1.5 py-0.5 rounded border border-slate-300 text-[10px] text-slate-700 hover:bg-slate-100">填入答案</button>
               <button data-act="origin-analysis" class="px-1.5 py-0.5 rounded border border-slate-300 text-[10px] text-slate-700 hover:bg-slate-100">填入解析</button>
             </div>
           </div>
         </details>`
      : '';

    const figuresHtml = col.value.figures.length
      ? `<div class="space-y-1.5">${col.value.figures.map((f, i) => `
          <div class="flex items-start gap-2 rounded-md border border-slate-200 bg-white p-1.5">
            <img src="${esc(f.url)}" alt="解析配图 ${i + 1}" class="w-12 h-12 object-contain bg-slate-50 rounded border border-slate-100 shrink-0">
            <div class="flex-1 space-y-1">
              <div class="flex items-center gap-1">
                <input data-fig-width="${i}" type="number" min="5" max="178" step="0.5" value="${f.display_width}" class="w-16 px-1 py-0.5 text-[10px] border border-slate-200 rounded">
                <span class="text-[10px] text-slate-400">毫米宽</span>
                <select data-fig-pos="${i}" class="text-[10px] px-1 py-0.5 border border-slate-200 rounded">
                  <option value="before" ${f.position === 'before' ? 'selected' : ''}>解析前</option>
                  <option value="after" ${f.position === 'after' ? 'selected' : ''}>解析后</option>
                  <option value="paragraph" ${f.position === 'paragraph' ? 'selected' : ''}>指定段后</option>
                </select>
                <button data-act="fig-remove" data-index="${i}" class="ml-auto text-[10px] text-rose-600 hover:text-rose-800">移除</button>
              </div>
            </div>
          </div>`).join('')}</div>`
      : '<div class="text-[10px] text-slate-400">还没有解析配图。可从原卷裁图，或直接粘贴截图。</div>';

    return `
      <div class="w-[400px] shrink-0 h-full flex flex-col rounded-lg border border-slate-200 bg-white shadow-sm overflow-hidden" data-col="${esc(col.id)}">
        <div class="flex items-center gap-1.5 px-2.5 py-1.5 border-b border-slate-100 bg-slate-50/70">
          <span class="font-bold text-slate-800 text-xs">第 ${esc(item.questionNumber)} 题</span>
          <span class="text-[10px] text-slate-500">${esc(item.questionType || '')}</span>
          <span class="text-amber-500 text-[10px]">${'★'.repeat(Number(item.difficultyStars) || 3)}</span>
          <span class="text-[10px] text-slate-400 truncate flex-1" title="${esc(item.source || '')}">${esc(item.source || '')}</span>
          <button data-act="close-col" class="text-slate-400 hover:text-slate-700 text-xs px-1">✕</button>
        </div>

        <div class="px-2.5 py-1 text-[10px] text-slate-500 border-b border-slate-100 truncate" title="${esc(col.status)}">${esc(col.status)}</div>

        <div class="flex-1 min-h-0 overflow-y-auto p-2.5 space-y-2.5">
          <div class="text-[11px] text-slate-700 bg-slate-50 rounded-md border border-slate-100 px-2 py-1.5 line-clamp-3">${esc(String(item.rawStem || item.content || '').replace(/\s+/g, ' ').slice(0, 220))}</div>

          <div>
            <div class="flex items-center justify-between mb-1">
              <span class="text-[10px] font-bold text-slate-600">答案</span>
            </div>
            <textarea data-field="answer" rows="2" class="w-full px-2 py-1.5 text-[11px] border border-slate-200 rounded-md focus:border-emerald-500 focus:outline-none resize-y font-mono">${esc(col.value.answer)}</textarea>
          </div>

          <div>
            <div class="flex items-center justify-between mb-1">
              <span class="text-[10px] font-bold text-slate-600">解析</span>
              <span class="text-[10px] text-slate-400">支持 LaTeX（$…$）</span>
            </div>
            <textarea data-field="analysis" rows="8" class="w-full px-2 py-1.5 text-[11px] border border-slate-200 rounded-md focus:border-emerald-500 focus:outline-none resize-y font-mono">${esc(col.value.analysis)}</textarea>
          </div>

          ${aiBox}
          ${originBox}

          <div class="space-y-1.5">
            <div class="flex items-center justify-between">
              <span class="text-[10px] font-bold text-slate-600">解析配图</span>
              <div class="flex gap-1">
                <button data-act="crop-open" class="px-1.5 py-0.5 rounded border border-slate-300 text-[10px] text-slate-700 hover:bg-slate-100">✂ 从原卷裁图</button>
                <button data-act="upload-open" class="px-1.5 py-0.5 rounded border border-slate-300 text-[10px] text-slate-700 hover:bg-slate-100">⬆ 上传图片</button>
                <input type="file" accept="image/png,image/jpeg,image/webp" class="hidden" data-act="upload-input">
              </div>
            </div>
            ${figuresHtml}
          </div>

          <div class="hidden rounded-md border border-emerald-200 bg-emerald-50/40 p-2 space-y-1.5" data-crop>
            <div class="flex items-center gap-1.5">
              <span class="text-[10px] font-bold text-emerald-900">原卷裁图</span>
              <select data-crop-page class="text-[10px] px-1 py-0.5 border border-slate-200 rounded bg-white"></select>
              <button data-act="crop-close" class="ml-auto text-[10px] text-slate-500 hover:text-slate-800">收起</button>
            </div>
            <div class="text-[10px] text-slate-500">在下面原卷页图上按住拖动框选区域，再点「裁剪插入」。</div>
            <div data-crop-surface class="relative select-none border border-slate-200 rounded overflow-hidden bg-slate-50">
              <img data-crop-img class="w-full block" alt="原卷页">
              <div data-crop-box class="absolute border-2 border-emerald-500 bg-emerald-400/20 hidden"></div>
            </div>
            <button data-act="crop-save" class="w-full py-1 rounded bg-emerald-600 text-white text-[10px] font-semibold disabled:opacity-40" disabled>裁剪插入</button>
          </div>
        </div>

        <div class="px-2.5 py-1.5 border-t border-slate-100 flex items-center gap-1.5 bg-slate-50/70">
          <button data-act="save" class="px-2.5 py-1 rounded-md bg-emerald-600 hover:bg-emerald-700 text-white text-[11px] font-semibold">保存本题</button>
          <button data-act="reload" class="px-2 py-1 rounded-md border border-slate-200 text-[11px] text-slate-600 hover:bg-slate-100">重新读取</button>
          <span data-col-status class="ml-auto text-[10px] text-slate-400 truncate"></span>
        </div>
      </div>`;
  }

  function renderColumns() {
    const host = document.getElementById('solutionEditorColumns');
    if (!host) return;
    if (!SOL.order.length) {
      host.innerHTML = '<div class="w-full text-center text-xs text-slate-400 py-16">左侧点题即可并排打开（最多 6 道）。</div>';
      return;
    }
    host.innerHTML = SOL.order.map(id => {
      const col = SOL.cols.get(id);
      return col ? columnHtml(col) : '<div class="w-[400px] shrink-0 rounded-lg border border-slate-200 bg-white p-3 text-[11px] text-slate-400">读取中…</div>';
    }).join('');
    bindColumnEvents(host);
    // 每个动作按钮直接绑定一次（委托只作兜底），点击时阻断冒泡，避免重复触发。
    host.querySelectorAll('button[data-act]').forEach((btn) => {
      if (btn.__solBound) return;
      btn.__solBound = true;
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const colNode = btn.closest('[data-col]');
        if (!colNode) return;
        try {
          onColumnAction(btn.dataset.act, colNode.dataset.col, btn);
        } catch (error) {
          setStatus((error && error.message) || '操作失败', 'error');
        }
      });
    });
  }

  /* ---------------- 事件绑定 ---------------- */

  function bindColumnEvents(host) {
    // 用事件委托：列会被反复重绘，逐节点绑定容易丢；委托一次搞定所有列。
    ensureDelegation();
  }

  let delegated = false;
  function ensureDelegation() {
    const host = document.getElementById('solutionEditorColumns');
    if (!host || delegated) return;
    delegated = true;

    host.addEventListener('click', (event) => {
      const btn = event.target.closest('button[data-act]');
      if (!btn || !host.contains(btn)) return;
      if (btn.__solBound) return;
      const colNode = btn.closest('[data-col]');
      if (!colNode) return;
      try {
        onColumnAction(btn.dataset.act, colNode.dataset.col, btn);
      } catch (error) {
        setStatus((error && error.message) || '操作失败', 'error');
      }
    });

    host.addEventListener('input', (event) => {
      const area = event.target.closest('textarea[data-field]');
      if (!area) return;
      const colNode = area.closest('[data-col]');
      if (!colNode) return;
      const col = SOL.cols.get(colNode.dataset.col);
      if (!col) return;
      col.value[area.dataset.field] = area.value;
      col.dirty = true;
      const tag = colNode.querySelector('[data-col-status]');
      if (tag) tag.innerText = '未保存';
    });

    host.addEventListener('change', (event) => {
      const el = event.target;
      const colNode = el.closest('[data-col]');
      if (!colNode) return;
      const col = SOL.cols.get(colNode.dataset.col);
      if (!col) return;
      if (el.matches('[data-fig-width]')) {
        const fig = col.value.figures[Number(el.dataset.figWidth)];
        if (!fig) return;
        fig.display_width = Math.round(Math.max(5, Math.min(178, Number(el.value) || 20)) * 100) / 100;
        el.value = fig.display_width;
        col.dirty = true;
      } else if (el.matches('[data-fig-pos]')) {
        const fig = col.value.figures[Number(el.dataset.figPos)];
        if (!fig) return;
        fig.position = el.value;
        col.dirty = true;
      } else if (el.matches('input[data-act="upload-input"]')) {
        uploadImage(col.id, el);
      } else if (el.matches('[data-crop-page]')) {
        const page = Number(el.value);
        col.crop.page = page;
        col.crop.range = null;
        const panel = el.closest('[data-crop]');
        const img = panel && panel.querySelector('[data-crop-img]');
        const box = panel && panel.querySelector('[data-crop-box]');
        const saveBtn = panel && panel.querySelector('[data-act="crop-save"]');
        if (box) box.classList.add('hidden');
        if (saveBtn) saveBtn.disabled = true;
        const documentId = col.item && col.item.documentId;
        if (img && documentId) img.src = `${window.API_BASE || ''}/api/documents/${encodeURIComponent(documentId)}/pages/${page}/preview`;
      }
    });
  }

  function onColumnAction(act, id, btn) {
    const col = SOL.cols.get(id);
    if (!col) { setStatus('这道题还没加载出来，先点「重新读取」', 'error'); return; }
    const node = document.querySelector(`#solutionEditorColumns [data-col="${id}"]`);
    if (!node) { setStatus('这一列没渲染出来，先点「重新读取」', 'error'); return; }

    if (act === 'close-col') { toggleColumn(id); return; }
    if (act === 'reload') { loadColumn(id); return; }
    if (act === 'save') { saveColumn(id); return; }

    if (act === 'adopt-answer' || act === 'origin-answer' || act === 'adopt-analysis' || act === 'origin-analysis') {
      const area = node.querySelector(`textarea[data-field="${act.endsWith('answer') ? 'answer' : 'analysis'}"]`);
      if (!area) { setStatus('这一列没渲染出输入框，先点「重新读取」', 'error'); return; }
      const text = act.startsWith('adopt') ? (col.ai && (col.ai.answer || col.ai.analysis) ? (act.endsWith('answer') ? col.ai.answer : col.ai.analysis) : '') : (act.endsWith('answer') ? (col.origin && col.origin.answer) : (col.origin && col.origin.analysis));
      area.value = text || '';
      col.value[area.dataset.field] = area.value;
      col.dirty = true;
      const tag = node.querySelector('[data-col-status]');
      if (tag) tag.innerText = '未保存';
      const fieldName = act.endsWith('answer') ? '答案' : '解析';
      const srcName = act.startsWith('adopt') ? 'AI 初稿' : '原卷';
      setStatus(`已把${srcName}填入${fieldName}（还没落库，点「保存本题」生效）`, 'ok');
      return;
    }

    if (act === 'fig-remove') {
      col.value.figures.splice(Number(btn.dataset.index), 1);
      col.dirty = true;
      renderColumns();
      return;
    }

    if (act === 'crop-open') { setStatus('正在读取原卷页…'); openCrop(id); return; }
    if (act === 'crop-close') { const box = node.querySelector('[data-crop]'); if (box) box.classList.add('hidden'); return; }
    if (act === 'crop-save') { saveCrop(id); return; }
    if (act === 'upload-open') { const input = node.querySelector('[data-act="upload-input"]'); if (input) input.click(); return; }
    if (act === 'upload-input') { uploadImage(id, btn); return; }
  }

  /* ---------------- 裁图 ---------------- */

  async function openCrop(id) {
    const col = SOL.cols.get(id);
    if (!col) return;
    const node = document.querySelector(`#solutionEditorColumns [data-col="${id}"]`);
    if (!node) return;
    const panel = node.querySelector('[data-crop]');
    if (!panel) return;
    panel.classList.remove('hidden');
    const select = panel.querySelector('[data-crop-page]');
    const img = panel.querySelector('[data-crop-img]');
    const box = panel.querySelector('[data-crop-box]');
    const saveBtn = panel.querySelector('[data-act="crop-save"]');
    col.crop.range = null;
    if (saveBtn) saveBtn.disabled = true;
    if (box) box.classList.add('hidden');

    const documentId = col.item && col.item.documentId;
    if (!documentId) {
      setStatus('这道题没有原卷来源，只能上传图片', 'error');
      return;
    }
    try {
      const res = await fetch(`${window.API_BASE || ''}/api/papers/${encodeURIComponent(documentId)}`);
      if (!res.ok) throw new Error(`原卷读取失败（HTTP ${res.status}）`);
      const body = await res.json();
      const pages = (body.paper && body.paper.pages) || body.pages || [];
      col.crop.pages = pages;
      select.innerHTML = pages.map(p => `<option value="${p.page_idx}">第 ${Number(p.page_idx) + 1} 页</option>`).join('');
      if (!pages.length) throw new Error('原卷没有可用页');
      const hint = col.item.content && col.item.content.sources && col.item.content.sources.find(s => Number.isInteger(s.page_idx));
      const fallback = (col.item.sourcePage !== undefined && col.item.sourcePage !== null) ? Number(col.item.sourcePage) : 0;
      const target = hint ? hint.page_idx : (pages.some(p => p.page_idx === fallback) ? fallback : pages[0].page_idx);
      select.value = String(target);
      col.crop.page = target;
      img.src = `${window.API_BASE || ''}/api/documents/${encodeURIComponent(documentId)}/pages/${Number(target)}/preview`;
      bindCropSurface(id, img, box);
    } catch (error) {
      setStatus((error && error.message) || '原卷裁图不可用', 'error');
    }
  }

  function bindCropSurface(id, img, box) {
    const col = SOL.cols.get(id);
    if (!col) return;
    let start = null;
    const toNorm = (event) => {
      const rect = img.getBoundingClientRect();
      const x = Math.max(0, Math.min(1000, Math.round(((event.clientX - rect.left) / rect.width) * 1000)));
      const y = Math.max(0, Math.min(1000, Math.round(((event.clientY - rect.top) / rect.height) * 1000)));
      return [x, y];
    };
    img.onmousedown = (event) => {
      event.preventDefault();
      start = toNorm(event);
      col.crop.range = null;
    };
    img.onmousemove = (event) => {
      if (!start) return;
      const point = toNorm(event);
      const range = [Math.min(start[0], point[0]), Math.min(start[1], point[1]), Math.max(start[0], point[0]), Math.max(start[1], point[1])];
      col.crop.range = range;
      box.classList.remove('hidden');
      box.style.left = `${range[0] / 10}%`;
      box.style.top = `${range[1] / 10}%`;
      box.style.width = `${(range[2] - range[0]) / 10}%`;
      box.style.height = `${(range[3] - range[1]) / 10}%`;
      const node = document.querySelector(`#solutionEditorColumns [data-col="${id}"]`);
      const saveBtn = node && node.querySelector('[data-act="crop-save"]');
      if (saveBtn) saveBtn.disabled = false;
    };
    img.onmouseup = () => { start = null; };
    img.onmouseleave = () => { start = null; };
  }

  async function saveCrop(id) {
    const col = SOL.cols.get(id);
    if (!col || !col.crop.range) return;
    const bbox = col.crop.range.map(Math.round);
    if (bbox[2] - bbox[0] < 10 || bbox[3] - bbox[1] < 10) {
      setStatus('选区太小，请重新框选', 'error');
      return;
    }
    try {
      const res = await fetch(`${window.API_BASE || ''}/api/library/${encodeURIComponent(id)}/solution-images`, {
        method: 'POST',
        headers: apiHeaders,
        body: JSON.stringify({ page_idx: Number(col.crop.page), bbox })
      });
      if (!res.ok) {
        let msg = `裁图失败（HTTP ${res.status}）`;
        try { const b = await res.json(); if (b && b.error) msg = b.error; } catch (e) { /* 用默认文案 */ }
        throw new Error(msg);
      }
      const body = await res.json();
      if (body && body.figure) {
        col.value.figures.push(normalizeFigures([body.figure])[0]);
        col.dirty = true;
        renderColumns();
        setStatus('裁图已插入，记得保存本题', 'ok');
      }
    } catch (error) {
      setStatus((error && error.message) || '裁图失败', 'error');
    }
  }

  async function uploadImage(id, input) {
    const col = SOL.cols.get(id);
    if (!col || !input.files || !input.files.length) return;
    const file = input.files[0];
    if (!/image\/(png|jpeg|webp)/.test(file.type)) { setStatus('只支持 PNG / JPEG / WebP 图片', 'error'); return; }
    if (file.size > 12 * 1024 * 1024) { setStatus('图片超过 12 MB，请先压缩', 'error'); return; }
    try {
      const form = new FormData();
      form.append('image', file, file.name || '解析图.png');
      const res = await fetch(`${window.API_BASE || ''}/api/library/${encodeURIComponent(id)}/solution-images`, {
        method: 'POST',
        headers: { 'X-QB-Request': '1' },
        body: form
      });
      if (!res.ok) throw new Error(`上传失败（HTTP ${res.status}）`);
      const body = await res.json();
      if (body && body.figure) {
        col.value.figures.push(normalizeFigures([body.figure])[0]);
        col.dirty = true;
        renderColumns();
        setStatus('图片已插入，记得保存本题', 'ok');
      }
    } catch (error) {
      setStatus((error && error.message) || '上传失败', 'error');
    } finally {
      input.value = '';
    }
  }

  /* ---------------- 保存 ---------------- */

  async function saveColumn(id) {
    const col = SOL.cols.get(id);
    if (!col) return false;
    const node = document.querySelector(`#solutionEditorColumns [data-col="${id}"]`);
    const tag = node && node.querySelector('[data-col-status]');
    if (tag) tag.innerText = '保存中…';
    const payload = {
      answer: col.value.answer || '',
      analysis: col.value.analysis || '',
      figures: col.value.figures.map(f => ({ id: f.id, display_width: f.display_width, position: f.position, paragraph: f.paragraph })),
      base_revision: col.base || null,
      sync_library: true
    };
    try {
      const res = await fetch(`${window.API_BASE || ''}/api/library/${encodeURIComponent(id)}/solution`, {
        method: 'POST',
        headers: apiHeaders,
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        let msg = `保存失败（HTTP ${res.status}）`;
        try { const b = await res.json(); if (b && b.error) msg = b.error; } catch (e) { /* 用默认文案 */ }
        throw new Error(msg);
      }
      const body = await res.json();
      if (body && body.solution) {
        col.base = body.base_revision || col.base;
        col.status = '已保存（正式解析已写回题库）';
      }
      col.dirty = false;
      if (tag) tag.innerText = '已保存';
      setStatus(`第 ${col.item ? col.item.questionNumber : ''} 题解析已保存`, 'ok');
      if (col.item) {
        col.item.savedSolution = { answer: col.value.answer, analysis: col.value.analysis, figures: col.value.figures };
        col.item.rawAnswer = col.value.answer || col.item.rawAnswer;
        col.item.rawAnalysis = col.value.analysis || col.item.rawAnalysis;
      }
      renderList();
      return true;
    } catch (error) {
      if (tag) tag.innerText = '保存失败';
      setStatus((error && error.message) || '保存失败', 'error');
      return false;
    }
  }

  async function saveAllSolutionColumns() {
    if (!SOL.order.length) { setStatus('没有打开任何题目', 'error'); return; }
    let ok = 0, fail = 0;
    for (const id of SOL.order.slice()) {
      const done = await saveColumn(id);
      if (done) ok += 1; else fail += 1;
    }
    setStatus(fail ? `保存完成：成功 ${ok} 道，失败 ${fail} 道` : `全部保存完成（${ok} 道）`, fail ? 'error' : 'ok');
  }

  /* ---------------- 对外接口 ---------------- */

  window.openSolutionEditor = openSolutionEditor;
  window.closeSolutionEditor = closeSolutionEditor;
  window.saveAllSolutionColumns = saveAllSolutionColumns;
  window.toggleSolutionColumn = toggleColumn;
  window.solutionEditorSearch = (keyword) => { SOL.keyword = keyword || ''; renderList(); };
  window.solutionEditorPickBasket = () => {
    const ids = (typeof basketQuestionIds !== 'undefined' ? Array.from(basketQuestionIds) : []).map(String);
    if (!ids.length) { setStatus('试题篮是空的', 'error'); return; }
    openSolutionEditor(ids);
  };
  window.solutionEditorPickFiltered = () => {
    const ids = allQuestions().slice(0, 4).map(q => String(q.id));
    if (!ids.length) { setStatus('当前筛选没有题', 'error'); return; }
    openSolutionEditor(ids);
  };
  window.solutionEditorPickMissing = () => {
    const ids = allQuestions()
      .filter(q => !q.savedSolution && !(q.rawAnalysis || q.rawAnswer))
      .slice(0, 4)
      .map(q => String(q.id));
    if (!ids.length) { setStatus('当前筛选里没有缺解析的题', 'ok'); return; }
    openSolutionEditor(ids);
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ensureDelegation);
  } else {
    ensureDelegation();
  }
})();
