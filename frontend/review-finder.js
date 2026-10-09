(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.QBReviewFinder = value;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  function normalize(value) {
    return String(value ?? "")
      .normalize("NFKC")
      .toLocaleLowerCase()
      .replace(/\\(arcsin|arccos|arctan|sin|cos|tan|cot|sec|csc)\b/g, "$1")
      .replace(/\\(?:left|right)\b/g, " ")
      .replace(/[\\${}^_]/g, "")
      .replace(/\s+/g, " ")
      .trim();
  }

  function parseQuery(value) {
    const raw = String(value ?? "").normalize("NFKC").trim();
    if (!raw) return { kind: "empty", value: "", terms: [] };
    const compact = raw.replace(/\s+/g, "");
    const page = compact.match(/^第?(\d+)页$/);
    if (page) return { kind: "page", value: Number(page[1]), terms: [] };
    const number = compact.match(/^(?:第)?(\d+)(?:题)?$/);
    if (number) return { kind: "number", value: Number(number[1]), terms: [] };
    return { kind: "text", value: raw, terms: normalize(raw).split(" ").filter(Boolean) };
  }

  function search(questions, value) {
    const query = parseQuery(value);
    if (query.kind === "empty") return [];
    return (questions || []).filter((question) => {
      if (query.kind === "number") return Number(question.number) === query.value;
      if (query.kind === "page") return (question.regions || []).some((region) => Number(region.page_idx) + 1 === query.value);
      const group = typeof question.group === "object" ? question.group?.title
        : typeof question.group === "string" ? question.group : question.group_name || question.section;
      const stem = question.edited_stem ?? question.stem;
      const options = question.edited_options ?? question.options ?? {};
      const text = normalize([stem, ...Object.values(options), group].filter(Boolean).join(" "));
      return query.terms.every((term) => text.includes(term));
    });
  }

  function snippet(question, maxLength = 92) {
    const stem = String(question?.edited_stem ?? question?.stem ?? "").trim();
    if (stem) return stem.length > maxLength ? `${stem.slice(0, maxLength)}…` : stem;
    const option = Object.values(question?.edited_options ?? question?.options ?? {}).find((value) => String(value || "").trim());
    if (option) {
      const text = String(option).trim();
      return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text;
    }
    return "原图题 · 暂无文字，可按题号或页码查找";
  }

  return { normalize, parseQuery, search, snippet };
});
