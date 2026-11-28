import katex from "katex";
import { marked } from "marked";
import DOMPurify from "dompurify";

export function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const MATH_PATTERN = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|\$([^$\n]+?)\$/g;

function renderTex(tex, displayMode) {
  try {
    return katex.renderToString(tex, { displayMode, throwOnError: false, trust: false, strict: "ignore" });
  } catch {
    return `<code>${escapeHtml(tex)}</code>`;
  }
}

/** 纯文本里的 $…$ / $$…$$ 渲染成公式，其余原样转义（题干、选项用）。 */
export function renderMathText(text) {
  const source = String(text ?? "");
  let html = "";
  let last = 0;
  for (const match of source.matchAll(MATH_PATTERN)) {
    html += escapeHtml(source.slice(last, match.index)).replace(/\n/g, "<br>");
    const display = match[1] ?? match[2];
    html += display !== undefined ? renderTex(display, true) : renderTex(match[3] ?? match[4], false);
    last = match.index + match[0].length;
  }
  html += escapeHtml(source.slice(last)).replace(/\n/g, "<br>");
  return html;
}

/** Markdown + KaTeX（心得、题包介绍用）。先把公式换成占位符，Markdown 渲染并消毒后再放回。 */
export function renderMarkdown(markdown) {
  const formulas = [];
  const protectedText = String(markdown ?? "").replace(MATH_PATTERN, (whole, d1, d2, i1, i2) => {
    const display = d1 ?? d2;
    formulas.push(display !== undefined ? renderTex(display, true) : renderTex(i1 ?? i2, false));
    return `KATEXSLOT${formulas.length - 1}END`;
  });
  const html = DOMPurify.sanitize(marked.parse(protectedText, { gfm: true, breaks: true, async: false }), {
    FORBID_TAGS: ["style", "form", "input", "iframe"],
    FORBID_ATTR: ["style"],
  });
  return html.replace(/KATEXSLOT(\d+)END/g, (_, index) => formulas[Number(index)] ?? "");
}

// 题型主键以题有据后端 core/qtypes.py 的 TYPE_LABELS 为准：题包导出时
// publication.question_type 原样带出，社区这边只是照着翻成中文。
// 漏一个键就会在页面上直接露出英文主键，所以这里必须跟后端保持全量一致。
const TYPE_NAMES = {
  single_choice: "单选题",
  multiple_choice: "多选题",
  fill_blank: "填空题",
  true_false: "判断题",
  free_response: "解答题",
  unknown: "题型未定",
  other: "其他",
};

export function typeName(key) {
  // 认不出的键一律归到「其他」，绝不把英文主键直接甩到页面上。
  return TYPE_NAMES[String(key ?? "")] || "其他";
}

export function timeAgo(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`;
  return date.toLocaleDateString("zh-CN");
}

export function megabytes(size) {
  const n = Number(size || 0);
  return n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
}

/** 分 → 「12」「9.90」，填表单用。 */
export function yuan(cents) {
  const n = Number(cents || 0);
  return n % 100 === 0 ? String(n / 100) : (n / 100).toFixed(2);
}

export function priceLabel(cents) {
  return Number(cents) > 0 ? `¥${yuan(cents)}` : "免费";
}
