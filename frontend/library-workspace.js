/* Small, shared rules for a question's safe preview and a stable selection. */
((root) => {
  "use strict";
  function summaryStem(value, renderer) {
    const source = String(value ?? "");
    const segments = renderer.typesetSegments(source);
    const boundaries = [];
    for (const segment of segments) {
      if (segment.type !== "text") continue;
      const text = source.slice(segment.start, segment.end);
      const breaks = /\n\s*\n|[。！？；]/g;
      for (const match of text.matchAll(breaks)) {
        const at = segment.start + match.index + match[0].length;
        if (at >= 45 && at < source.length - 8) boundaries.push(at);
      }
    }
    // Keep every formula intact. If no safe prose boundary exists, show the
    // whole paragraph rather than cutting a denominator or a condition.
    const end = boundaries.find((at) => at >= 140) || (source.length > 280 ? boundaries[0] : null);
    return { text: end ? source.slice(0, end).trim() : source, folded: Boolean(end) };
  }
  function uniqueIds(values, limit = 500) {
    return [...new Set((Array.isArray(values) ? values : []).filter((id) => typeof id === "string" && id.length <= 80))].slice(0, limit);
  }
  function moveWithinGroup(ids, item, neighbor) {
    const order = [...ids], from = order.indexOf(item), to = order.indexOf(neighbor);
    if (from >= 0 && to >= 0) [order[from], order[to]] = [order[to], order[from]];
    return order;
  }
  /* 1.25.93：一颗标签是什么颜色，**只在这里算一次**，各处都调它。
     以前只有教材目录真算了颜色（library.js 里的 directoryColor）；标签管理、标签选择
     窗口、题目详情、题卡各自只看「这颗标签自己有没有设颜色」—— 而老师的标签几乎都
     没设过颜色（302 个里只有 17 个章设了），于是同一个标签在五个地方是五种颜色：
     目录里是蓝的、标签管理里是灰绿的、选择窗口里是灰的、详情里是灰的、题卡上一律绿。
     规则：自己设的颜色 → 同型祖先的颜色 → 按类型/学段从调色板取。 */
  const TAG_HEX = /^#[0-9a-f]{6}$/i;
  // 1.25.93：回退色必须跟老师自己给章设的颜色对得上。
  // 老师给初中章设的是蓝 #477a9e、高中章设的是绿 #387f72、方法章设的是紫 #8069a3。
  // 早先这里写的是「初中绿、高中蓝」，正好跟老师的选择反着 —— 于是「平面向量的应用」
  // 这种既没有上级、又同时属于初中和高中的标签掉进回退色，在 98 颗蓝的初中知识点里
  // 成了唯一一颗绿。回退色只在一颗标签实在找不到出处时才用，别再跟老师的选择拧着来。
  const TAG_STAGE_COLOR = {
    knowledge: { 初中: "#477a9e", 高中: "#387f72", 默认: "#5b6770" },
    method: "#8069a3",
    custom: "#8965a5",
    legacy: "#5b6770",
  };
  function tagStageOf(tag) {
    if (!tag) return "";
    if (Array.isArray(tag.levels) && tag.levels.length) return String(tag.levels[0]);
    const name = String(tag.name || "");
    if (name.startsWith("初中")) return "初中";
    if (name.startsWith("高中")) return "高中";
    return "";
  }
  // 1.25.93：一颗标签自己没有颜色、也找不到同型祖先的颜色时，先去问「同一个学段、
  // 同一种类型里，那些顶级的章自己是什么色」，跟着它走。
  // 这么一改，老师改了某一章的颜色，这一章底下的标签也跟着变；写死的回退色只在
  // 实在问不出来时才用（那几个顶级章颜色不一致时也算问不出来）。
  function stageChapterColor(tag, rows) {
    const stage = tagStageOf(tag);
    if (!stage) return "";
    const colors = new Set();
    rows.forEach(row => {
      if (!row || row.kind !== tag.kind) return;
      if (tagStageOf(row) !== stage) return;
      if ((row.parent_ids || []).length) return;
      if (row.color && TAG_HEX.test(String(row.color))) colors.add(String(row.color).toLowerCase());
    });
    return colors.size === 1 ? [...colors][0] : "";
  }
  function tagColor(tag, catalogue) {
    if (!tag) return "";
    const rows = Array.isArray(catalogue) ? catalogue : [];
    const byId = new Map(rows.map(row => [String(row.id), row]));
    const seen = new Set();
    let current = tag;
    while (current) {
      const id = String(current.id);
      if (seen.has(id)) break;
      seen.add(id);
      if (current.color && TAG_HEX.test(String(current.color))) return String(current.color).toLowerCase();
      const parentId = (current.parent_ids || []).map(String).find(pid => byId.get(pid)?.kind === current.kind);
      if (!parentId) break;
      current = byId.get(parentId);
    }
    const fromChapter = stageChapterColor(tag, rows);
    if (fromChapter) return fromChapter;
    const map = TAG_STAGE_COLOR[tag.kind];
    if (typeof map === "string") return map;
    if (map && typeof map === "object") {
      const stage = tagStageOf(tag);
      if (stage === "初中") return map.初中;
      if (stage === "高中") return map.高中;
      return map.默认 || "";
    }
    return "";
  }
  const api = { summaryStem, uniqueIds, moveWithinGroup, tagColor, tagStageOf };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LibraryWorkspace = api;
})(typeof window === "undefined" ? globalThis : window);
