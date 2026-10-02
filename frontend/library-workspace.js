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
  const api = { summaryStem, uniqueIds, moveWithinGroup };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LibraryWorkspace = api;
})(typeof window === "undefined" ? globalThis : window);
