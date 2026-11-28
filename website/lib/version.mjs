// 官网首页「静态版本号兜底」的唯一真源。
//
// 背景：首页 HTML 里写死的版本号（v2.3.12）每发一次版就过期一次。
// 爬虫、社交分享卡片、JS 没跑起来或 /api/latest-release 超时的浏览器，
// 看到的都是这个写死的值 —— 实测已落后真实版本 3 个小版本。
//
// 做法：worker 运行时用 R2 里每 5 分钟刷新的真实最新版，
// 就地替换掉首页两处版本号。写死的值退化成「worker 也挂了」的最后一层兜底。
//
// 替换必须按 id 限定作用域：全页有 8 个 <strong>，全局替换会误伤正文。

const BADGE_RE = /(<span\b[^>]*\bid="versionBadge"[^>]*>)([^<]*)(<\/span>)/i;
const META_BLOCK_RE = /(<p\b[^>]*\bid="releaseMeta"[^>]*>)([\s\S]*?)(<\/p>)/i;
const META_STRONG_RE = /(<strong>)([^<]*)(<\/strong>)/i;
const VERSION_SHAPE_RE = /^v?\d+(?:\.\d+)*$/;

function normalizeTag(tag) {
  const clean = String(tag || "").trim();
  if (!clean) return "";
  return clean.startsWith("v") ? clean : `v${clean}`;
}

// 只替换「看起来就是版本号」的文本，避免页面结构变了以后把正文搅了。
function replaceVersionText(oldText, version) {
  if (!VERSION_SHAPE_RE.test(String(oldText || "").trim())) return null;
  return version;
}

/**
 * 把真实最新版写进首页 HTML 的两处版本号。
 * @param {string} html 完整首页 HTML
 * @param {string} tag  版本号，如 "v2.3.15"
 * @returns {string} 注入后的 HTML；结构不匹配时原样返回
 */
export function injectVersion(html, tag) {
  const version = normalizeTag(tag);
  if (!version || typeof html !== "string" || !html) return html;

  let out = html.replace(BADGE_RE, (match, open, oldText, close) => {
    const next = replaceVersionText(oldText, version);
    return next ? `${open}${next}${close}` : match;
  });

  out = out.replace(META_BLOCK_RE, (match, open, inner, close) => {
    const patchedInner = inner.replace(META_STRONG_RE, (strongMatch, sOpen, sOld, sClose) => {
      const next = replaceVersionText(sOld, version);
      return next ? `${sOpen}${next}${sClose}` : strongMatch;
    });
    return patchedInner === inner ? match : `${open}${patchedInner}${close}`;
  });

  return out;
}

/**
 * 从首页 HTML 里读出当前显示的版本号（先徽章，再 hero 文案）。
 * @param {string} html 完整首页 HTML
 * @returns {string} 版本号；读不到返回空串
 */
export function extractVersion(html) {
  if (typeof html !== "string" || !html) return "";
  const badge = BADGE_RE.exec(html);
  if (badge && badge[2].trim()) return badge[2].trim();
  const meta = META_BLOCK_RE.exec(html);
  if (meta) {
    const strong = META_STRONG_RE.exec(meta[2]);
    if (strong && strong[2].trim()) return strong[2].trim();
  }
  return "";
}
