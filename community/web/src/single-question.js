import { escapeHtml, renderMathText, typeName } from "./render.js";

/** 单题与随题笔记使用同一条阅读顺序；多题题包不调用此版式。 */
export function singleQuestionHtml(preview) {
  const sample = preview.samples[0];
  const media = preview.single_question || {};
  const safeImages = (images) => (Array.isArray(images) ? images : []).filter(
    (value) => typeof value === "string" && /^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(value),
  );
  const images = (items, name, className) => safeImages(items).map((image, index) =>
    `<figure class="${className}"><button type="button" class="single-image-zoom" data-share-image="${escapeHtml(image)}" aria-label="放大${name} ${index + 1}"><img src="${escapeHtml(image)}" alt="${name} ${index + 1}" loading="lazy" decoding="async"></button><figcaption>${name} ${index + 1} · 点击放大</figcaption></figure>`,
  ).join("");
  const options = (sample.options || []).map((item) =>
    `<li><span class="single-option-key">${escapeHtml(item.key)}.</span><div>${renderMathText(item.text)}</div></li>`,
  ).join("");
  const notePages = safeImages(media.onenote_pages);
  const legacyNote = !preview.single_question && preview.onenote_preview ? [preview.onenote_preview] : [];
  const source = preview.papers?.[0]?.title || "";
  return `<article class="single-question-share" aria-label="单题分享与作者笔记">
    <header class="single-share-head"><span class="single-share-kicker">精选单题</span><span>${escapeHtml(typeName(sample.question_type))}${sample.number ? ` · 原题第 ${escapeHtml(sample.number)} 题` : ""}</span></header>
    <section class="single-share-problem" aria-labelledby="singleProblemHeading">
      <h2 id="singleProblemHeading">题目</h2>
      ${sample.stem ? `<div class="single-share-stem">${renderMathText(sample.stem)}</div>` : ""}
      ${images(media.question_images, "原图题面", "single-body-image")}
      ${!sample.stem && !safeImages(media.question_images).length ? '<p class="single-share-muted">原图题面请下载题目后查看。</p>' : ""}
      ${options ? `<ol class="single-share-options">${options}</ol>` : ""}
      ${images(media.figures, "题目配图", "single-figure-image")}
      ${sample.figures && !safeImages(media.figures).length ? `<p class="single-share-muted">本题含 ${escapeHtml(sample.figures)} 张配图，下载后查看。</p>` : ""}
      ${source ? `<p class="single-share-source">题源 · ${escapeHtml(source)}</p>` : ""}
    </section>
    ${preview.has_onenote_notes ? `<section class="single-share-notes" aria-label="作者 OneNote 思考步骤">
      <div class="single-notes-heading"><span class="single-notes-mark" aria-hidden="true">✎</span><div><h2>作者的 OneNote 思路</h2><p>${media.notes_locked ? "完整笔记随题目下载提供" : notePages.length ? `${notePages.length} 页笔记，按原顺序阅读` : "随题附带的笔记预览"}</p></div></div>
      ${media.notes_locked ? '<p class="single-notes-locked">获取这道题后，可查看作者随题分享的完整思考步骤。</p>' : images(notePages.length ? notePages : legacyNote, "OneNote 笔记", "single-note-page")}
      ${!media.notes_locked && !notePages.length && !legacyNote.length ? '<p class="single-share-muted">笔记已随题附带，下载后查看。</p>' : ""}
    </section>` : ""}
    ${media.images_omitted ? '<p class="single-share-muted single-share-limit">部分图片未能生成网页预览，请下载后查看完整内容。</p>' : ""}
    <footer class="single-share-footer"><span>题有据 · 单题分享</span><button type="button" id="singleQuestionCopyLink" class="btn">复制分享链接</button></footer>
    <dialog id="singleShareImageDialog" class="single-share-image-dialog" aria-label="放大查看题目或笔记">
      <div class="single-share-image-tools"><span>可滚动查看完整图片</span><button type="button" class="btn" data-close-share-image>关闭</button></div>
      <div class="single-share-image-scroll"><img alt="" id="singleShareLargeImage"></div>
    </dialog>
  </article>`;
}

export function bindSingleQuestionImages(root = document) {
  const dialog = root.querySelector("#singleShareImageDialog");
  if (!dialog) return;
  const image = dialog.querySelector("#singleShareLargeImage");
  for (const button of root.querySelectorAll("[data-share-image]")) {
    button.addEventListener("click", () => {
      image.src = button.dataset.shareImage;
      image.alt = button.getAttribute("aria-label");
      dialog.showModal();
    });
  }
  dialog.querySelector("[data-close-share-image]").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
}
