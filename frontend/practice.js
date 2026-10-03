(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const paper = window.location.pathname.split("/").filter(Boolean).at(-1);
  const base = `/api/demo/${encodeURIComponent(paper)}`;
  const key = `qb-practice-basket:${paper}`;
  let items = [], selected = new Set(), generation = 0, busy = false, ready = false, requestController = null;
  try { const saved = JSON.parse(localStorage.getItem(key) || "[]"); if (Array.isArray(saved)) selected = new Set(saved.filter(x => typeof x === "string")); } catch { /* Practice continues without storage. */ }
  $("practiceReturn").href = `/?paper=${encodeURIComponent(paper)}`;
  const node = (tag, cls, text) => { const n = document.createElement(tag); n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  function progress(lesson, completed = false) {
    try {
      const old = JSON.parse(localStorage.getItem("qb-teach") || "null");
      if (!old || old.paper !== paper || old.version !== 3) return;
      localStorage.setItem("qb-teach", JSON.stringify({ ...old, lesson, completed, course: "basic", practiceUrl: `/practice/${paper}` }));
    } catch { /* The practice remains usable without persistence. */ }
  }
  function error(message = "") { $("practiceError").textContent = message; $("practiceError").hidden = !message; }
  function controls() {
    const count = selected.size;
    $("practiceCount").textContent = `已选 ${count} 题`;
    $("practicePreview").disabled = busy || !count;
    $("practiceExport").hidden = !ready;
    $("practiceExport").disabled = busy || !ready;
    for (const box of $("practiceList").querySelectorAll("input")) box.disabled = busy;
  }
  function invalidate(record = true, advance = true) {
    if (advance) generation++;
    ready = false;
    $("practicePreviewPanel").hidden = true;
    $("practiceFrame").removeAttribute("srcdoc");
    $("practiceDone").hidden = true;
    $("stepSelect").setAttribute("aria-current", "step");
    $("stepPreview").removeAttribute("aria-current"); $("stepExport").removeAttribute("aria-current");
    $("practiceGuidance").textContent = selected.size ? "已选好题，点击预览检查练习卷。" : "勾选一题，加入这次练习卷。";
    if (record && selected.size) progress("basket");
    try { localStorage.setItem(key, JSON.stringify([...selected])); } catch { /* No real basket is used. */ }
    controls();
  }
  function payload() { return { ids: [...selected], fingerprints: Object.fromEntries(items.filter(x => selected.has(x.id)).map(x => [x.id, x.fingerprint])) }; }
  async function request(path, body) {
    const controller = new AbortController(); requestController = controller;
    try {
      const response = await fetch(`${base}${path}`, { method: body ? "POST" : "GET", cache: "no-store", signal: controller.signal,
        headers: body ? { "Content-Type": "application/json", "X-QB-Request": "1" } : {}, body: body ? JSON.stringify(body) : undefined });
      if (!response.ok) { const data = await response.json().catch(() => ({})); throw new Error(data.error || "练习暂时无法读取，请重试。"); }
      return response;
    } finally { if (requestController === controller) requestController = null; }
  }
  async function load() {
    if (busy) return;
    busy = true; error(); controls(); $("practiceRetry").hidden = true; const token = ++generation;
    try {
      const data = await (await request("/library")).json();
      if (token !== generation) return;
      items = data.items;
      selected = new Set([...selected].filter(id => items.some(x => x.id === id)));
      $("practiceList").replaceChildren();
      if (!items.length) $("practiceList").append(node("p", "helper", "还没有通过的练习题。返回核对，确认后点通过，再来选题。"));
      for (const item of items) {
        const card = node("article", "practice-card"), label = node("label", "practice-select"), input = node("input", "");
        input.type = "checkbox"; input.checked = selected.has(item.id);
        input.addEventListener("change", () => { input.checked ? selected.add(item.id) : selected.delete(item.id); error(); invalidate(); });
        label.append(input, node("span", "", `选入第 ${item.number} 题`));
        const body = node("div", "practice-question");
        window.QBRender.renderQuestion(body, item.content, { number: item.number, showAnswer: "none", resolveUrl: x => x.url, resolveQuestionImageUrl: x => x.url });
        card.append(label, body); $("practiceList").append(card);
      }
      invalidate(false, false);
    } catch (e) { if (token === generation) { error(e.message); $("practiceRetry").hidden = false; } }
    finally { if (token === generation) { busy = false; controls(); } }
  }
  async function preview() {
    if (busy || !selected.size) return;
    busy = true; ready = false; error(); controls(); const token = ++generation;
    $("practiceGuidance").textContent = "正在准备预览…";
    try {
      const source = await (await request("/preview", payload())).text();
      if (token !== generation) return;
      const frame = $("practiceFrame");
      let loaded = false;
      const onLoad = () => { loaded = true; };
      frame.addEventListener("load", onLoad, { once: true });
      $("practicePreviewPanel").hidden = false; frame.srcdoc = source;
      const started = Date.now();
      const result = await new Promise((resolve, reject) => {
        const check = () => {
          if (token !== generation) { reject(new Error("选题已改变，请重新预览。")); return; }
          const status = loaded ? frame.contentWindow?.__qbPdfStatus : null;
          if (status?.error) { reject(new Error(status.error)); return; }
          if (status?.ready) { resolve(status); return; }
          if (Date.now() - started > 45000) { reject(new Error("练习卷排版超时，请重新预览。")); return; }
          setTimeout(check, 100);
        }; check();
      }).finally(() => frame.removeEventListener("load", onLoad));
      if (token !== generation) return;
      ready = true; $("practicePages").textContent = `${selected.size} 题 · ${result.page_count} 页`;
      frame.style.height = `${frame.contentDocument.documentElement.scrollHeight + 24}px`;
      $("stepSelect").removeAttribute("aria-current"); $("stepPreview").setAttribute("aria-current", "step");
      $("practiceGuidance").textContent = "预览已准备好。检查题目完整后，点击导出 PDF。";
      progress("export");
    } catch (e) { if (token === generation) { error(e.message); $("practiceGuidance").textContent = "预览未完成，请检查提示后重试。"; } }
    finally { if (token === generation) { busy = false; controls(); } }
  }
  async function download() {
    if (busy || !ready) return;
    busy = true; error(); controls(); const token = ++generation, chosen = payload();
    try {
      const response = await request("/export-pdf", chosen), data = await response.blob();
      if (token !== generation || JSON.stringify(chosen) !== JSON.stringify(payload())) return;
      if (data.type !== "application/pdf" || data.size < 100) throw new Error("PDF 未完整生成，请重试。");
      const pages = Number(response.headers.get("X-Page-Count"));
      const previewPages = Number($("practiceFrame").contentWindow?.__qbPdfStatus?.page_count);
      if (!pages || pages !== previewPages) { ready = false; throw new Error("排版有变化，请重新预览后导出。"); }
      const url = URL.createObjectURL(data), link = document.createElement("a");
      link.href = url; link.download = "新手练习卷.pdf"; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
      progress("finish", true); $("practiceDone").hidden = false;
      $("stepPreview").removeAttribute("aria-current"); $("stepExport").setAttribute("aria-current", "step");
      $("practiceGuidance").textContent = "PDF 已生成并交给浏览器下载。练习完成。";
    } catch (e) { if (token === generation) error(e.message); }
    finally { if (token === generation) { busy = false; controls(); } }
  }
  $("practicePreview").addEventListener("click", preview); $("practiceExport").addEventListener("click", download); $("practiceRetry").addEventListener("click", load);
  window.addEventListener("pagehide", () => { requestController?.abort(); requestController = null; busy = false; invalidate(false); });
  window.addEventListener("pageshow", event => { if (event.persisted) void load(); });
  void load();
})();
