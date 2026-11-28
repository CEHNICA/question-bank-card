/* 随题保存的作者笔记是只读图片；阅读它们不调用 OneNote COM。 */
((root) => {
  "use strict";
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function figuresHtml(solution, apiBase = "") {
    return (Array.isArray(solution?.figures) ? solution.figures : []).filter(figure =>
      /^\/api\/library\/[0-9a-f-]{36}\/solution-images\/[0-9a-f-]{36}(?:\?v=[a-f0-9]+)?$/i.test(figure?.url || "")
    ).map((figure, index) => `<figure style="margin:12px 0"><img src="${escape(apiBase + figure.url)}" alt="笔记或解析配图 ${index + 1}" loading="lazy" style="display:block;max-width:100%;height:auto"><figcaption style="font-size:12px;color:#64748b">第 ${index + 1} 张 · 可右键保存</figcaption></figure>`).join("");
  }
  async function open(publicationId, apiBase = "") {
    const dialog = document.createElement("dialog");
    dialog.setAttribute("aria-label", "作者的 OneNote 笔记");
    dialog.style.cssText = "width:min(860px,94vw);max-height:88vh;padding:20px;border:1px solid #e2e8f0;border-radius:12px;background:white;color:#172d46;overflow:auto";
    dialog.innerHTML = '<header style="display:flex;justify-content:space-between;gap:16px;align-items:center"><h2 style="font-size:18px;font-weight:700">作者的 OneNote 笔记</h2><button type="button" style="padding:8px 12px;border:1px solid #cbd5e1;border-radius:6px">关闭</button></header><p style="font-size:13px;color:#64748b;margin:12px 0">随题保存的只读笔记，无需打开 OneNote。你自己的做题记录仍保存在自己的笔记本中。</p><section aria-live="polite">正在读取笔记…</section>';
    dialog.querySelector("button").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    document.body.append(dialog);
    dialog.showModal();
    const body = dialog.querySelector("section");
    try {
      const response = await fetch(`${apiBase}/api/library/${encodeURIComponent(publicationId)}/onenote/shared`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "笔记读取失败，请重新下载题包");
      const notes = Array.isArray(data.notes) ? data.notes : [];
      body.innerHTML = notes.map(note => `${note.needs_check ? '<p>题面已有变化，请对照原笔记核对。</p>' : ''}${figuresHtml(note, apiBase)}`).join("") || "题包中没有可用的作者笔记，请作者重新分享包含笔记的题包。";
      body.querySelectorAll("img").forEach(image => image.addEventListener("error", () => {
        const warning = document.createElement("p");
        warning.textContent = "笔记图片缺失或校验失败，请重新下载题包。";
        image.replaceWith(warning);
      }, { once: true }));
    } catch (error) { body.textContent = error.message || "笔记读取失败"; }
  }
  const api = Object.freeze({ figuresHtml, open });
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.SharedOneNote = api;
})(typeof window === "undefined" ? globalThis : window);
