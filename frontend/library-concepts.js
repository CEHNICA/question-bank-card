/* 题有据 · 找题与整理工作台
 *
 * 单独一个页面，不改 library.js —— 那文件有七千行，119 个前端测试按源码文本
 * 断言，动它会连带炸一片。这里的逻辑自己一份，改动只落在这个文件里。
 *
 * 三样东西各自独立：教材目录（在哪本书哪一节）、数学主题（涉及什么内容或方法）、
 * 个人专题（老师准备怎么用题）。它们不是一套东西的三级分类。
 *
 * 正式关联带用途：主要考查 / 辅助用到 / 解法采用 / 题面特征。
 * 用途记在关联上，不记在概念上 —— 同一个「分类讨论」在不同题里角色不同。
 *
 * 检索结果分三块：已确认匹配、旧标签命中、待核对线索。分开的，不混在一起数。
 */

(() => {
  const root = typeof window !== "undefined" ? window : globalThis;

  const LIST_LIMIT = 50;

  const ROLE_LABELS = {
    primary: "主要考查",
    supporting: "辅助用到",
    method: "解法采用",
    feature: "题面特征",
    legacy: "旧关联，作用未细分",
  };

  const ENTRY_LABELS = {
    textbook: "教材目录",
    concept: "数学主题",
    collection: "个人专题",
  };

  // 题型在库里是英文代码，界面上给中文。
  const TYPE_LABELS = {
    single_choice: "选择题",
    multiple_choice: "多选题",
    fill_blank: "填空题",
    free_response: "解答题",
    proof: "证明题",
    calculation: "计算题",
  };

  const state = {
    entry: "textbook",
    query: emptyQuery(),
    results: null,
    selected: null,
    detail: null,
    conversion: null,
    railData: { textbooks: [], chapters: [], collections: [], concepts: [] },
    message: "",
  };

  function emptyQuery() {
    return {
      query_version: 2,
      keywords: [],
      filters: {},
      concept_groups: [],
      method_groups: [],
      exclusions: { conditions: [], handling_of_unknown: "separate" },
      include_clues: false,
      sort: "recent",
    };
  }

  // ---------------------------------------------------------------- 纯逻辑

  /** 把当前查询压成一排「可移除的条件条」。 */
  function conditionChips(query) {
    const chips = [];
    (query.keywords || []).forEach((word) => {
      chips.push({ kind: "keyword", text: `关键词：${word}`, remove: { path: "keywords", value: word } });
    });
    const filters = query.filters || {};
    Object.keys(filters).forEach((key) => {
      if (!filters[key]) return;
      const label = {
        source: "来源",
        textbook: "教材",
        chapter: "章节",
        stage: "学段",
        question_type: "题型",
        collection: "专题",
        difficulty: "难度",
      }[key] || key;
      chips.push({ kind: "filter", text: `${label}：${filters[key]}`, remove: { path: `filters.${key}` } });
    });
    (query.concept_groups || []).forEach((group, index) => {
      const roles = (group.roles || []).map((role) => ROLE_LABELS[role] || role).join("、");
      chips.push({
        kind: "concept",
        text: `概念：${(group.names || group.ids || []).join("、")}（${roles}）`,
        remove: { path: `concept_groups.${index}` },
      });
    });
    (query.method_groups || []).forEach((group, index) => {
      const scope = group.subquestion ? `第（${group.subquestion}）问` : "整题";
      const same = group.cooccurrence === "same_variant" ? "同一个解法里" : "分别存在";
      chips.push({
        kind: "method",
        text: `方法：${(group.names || group.ids || []).join("、")}（${scope}，${same}）`,
        remove: { path: `method_groups.${index}` },
      });
    });
    if ((query.exclusions?.conditions || []).length) {
      chips.push({
        kind: "exclusion",
        text: `排除：${query.exclusions.conditions.length} 项`,
        remove: { path: "exclusions.conditions" },
      });
    }
    if (query.include_clues) {
      chips.push({ kind: "clues", text: "连待核对线索一起看", remove: { path: "include_clues" } });
    }
    return chips;
  }

  /** 把结果按三块分开，各数各的。旧记录不冒充正式匹配。 */
  function partitionResults(result) {
    const confirmed = [];
    const legacy = [];
    const clues = [];
    const unreviewed = [];
    (result?.items || []).forEach((item) => {
      if (item.bucket === "confirmed") confirmed.push(item);
      else if (item.bucket === "legacy") legacy.push(item);
      else if (item.bucket === "unreviewed") unreviewed.push(item);
      else clues.push(item);
    });
    return { confirmed, legacy, clues, unreviewed };
  }

  /** 去掉查询里的某个条件。 */
  function removeCondition(query, remove) {
    const next = JSON.parse(JSON.stringify(query));
    if (!remove) return next;
    if (remove.path === "include_clues") {
      next.include_clues = false;
      return next;
    }
    if (remove.path.startsWith("filters.")) {
      delete next.filters[remove.path.slice("filters.".length)];
      return next;
    }
    if (remove.path.startsWith("concept_groups.")) {
      next.concept_groups.splice(Number(remove.path.split(".")[1]), 1);
      return next;
    }
    if (remove.path.startsWith("method_groups.")) {
      next.method_groups.splice(Number(remove.path.split(".")[1]), 1);
      return next;
    }
    if (remove.path === "exclusions.conditions") {
      next.exclusions.conditions = [];
      return next;
    }
    if (remove.path === "keywords" && remove.value) {
      next.keywords = next.keywords.filter((word) => word !== remove.value);
      return next;
    }
    return next;
  }

  // ---------------------------------------------------------------- 请求

  async function api(path, options = {}) {
    const { method = "GET", body } = options;
    const headers = {};
    if (method !== "GET") headers["X-QB-Request"] = "1";
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const response = await fetch(path, {
      method,
      cache: "no-store",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (error) {
      payload = null;
    }
    if (!response.ok) {
      const message = (payload && payload.error) || `请求失败（${response.status}）`;
      throw new Error(message);
    }
    return payload;
  }

  function newRequestId() {
    if (root.crypto && root.crypto.randomUUID) return root.crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
      const random = (Math.random() * 16) | 0;
      const value = char === "x" ? random : (random & 0x3) | 0x8;
      return value.toString(16);
    });
  }

  // ---------------------------------------------------------------- DOM

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  const ui = {
    rail: null,
    railBody: null,
    chips: null,
    summary: null,
    list: null,
    inspector: null,
    search: null,
    savedSelect: null,
  };

  function bindUi() {
    ui.rail = document.getElementById("conceptsRail");
    ui.railBody = document.getElementById("conceptsRailBody");
    ui.chips = document.getElementById("conceptsChips");
    ui.summary = document.getElementById("conceptsSummary");
    ui.list = document.getElementById("conceptsList");
    ui.inspector = document.getElementById("conceptsInspector");
    ui.search = document.getElementById("conceptsSearch");
    ui.savedSelect = document.getElementById("conceptsSavedSearches");

    ui.rail.querySelectorAll(".concepts-rail-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        state.entry = tab.dataset.entry;
        ui.rail.querySelectorAll(".concepts-rail-tab").forEach((other) => {
          const active = other === tab;
          other.classList.toggle("is-active", active);
          other.setAttribute("aria-selected", active ? "true" : "false");
        });
        renderRail();
      });
    });

    document.getElementById("conceptsSearchGo").addEventListener("click", () => runSearch());
    ui.search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        runSearch();
      }
    });
    document.getElementById("conceptsSaveSearch").addEventListener("click", () => saveCurrentSearch());
    ui.savedSelect.addEventListener("change", () => {
      const found = (state.savedSearches || []).find((item) => item.id === ui.savedSelect.value);
      if (!found) return;
      state.query = found.query;
      renderChips();
      runSearch();
    });
  }

  // ---------------------------------------------------------------- 左栏

  async function loadRail() {
    const [textbooks, chapters, collections] = await Promise.all([
      api("/api/library/textbooks"),
      api("/api/library/chapters"),
      api("/api/library/collections"),
    ]);
    state.railData.textbooks = textbooks.textbooks || [];
    state.railData.chapters = chapters.chapters || [];
    state.railData.collections = collections.collections || [];
    renderRail();
  }

  function renderRail() {
    if (!ui.railBody) return;
    ui.railBody.replaceChildren();
    const title = el("h2", "concepts-rail-title", ENTRY_LABELS[state.entry]);
    ui.railBody.append(title);

    if (state.entry === "textbook") {
      const note = el(
        "p",
        "concepts-hint",
        "教材目录只记「在哪本书的哪一节」。不知道版次的旧章节在「通用教学目录」里，不编造版本。",
      );
      ui.railBody.append(note);
      const textbooks = state.railData.textbooks || [];
      if (!textbooks.length) {
        ui.railBody.append(el("p", "concepts-hint", "还没有教材目录。"));
        return;
      }
      textbooks.forEach((textbook) => {
        const block = el("section", "concepts-rail-block");
        block.append(el("h3", "concepts-rail-sub", textbook.name));
        const chapters = (state.railData.chapters || []).filter(
          (chapter) => chapter.textbook_id === textbook.id,
        );
        if (!chapters.length) {
          block.append(el("p", "concepts-hint", "这一本还没有章节。"));
        }
        chapters.forEach((chapter) => {
          const button = el("button", "concepts-rail-item", chapter.title);
          button.type = "button";
          button.addEventListener("click", () => {
            state.query = emptyQuery();
            state.query.filters.chapter = chapter.id;
            state.query.filters.textbook = textbook.id;
            renderChips();
            runSearch();
          });
          block.append(button);
        });
        ui.railBody.append(block);
      });
      return;
    }

    if (state.entry === "concept") {
      const note = el(
        "p",
        "concepts-hint",
        "数学主题是概念，不是教材章节。「包含下级」只是把检索范围放宽，不会给题目复制父概念标签。",
      );
      ui.railBody.append(note);
      const input = el("input", "concepts-rail-search");
      input.type = "search";
      input.placeholder = "找概念…";
      input.addEventListener("input", () => {
        const text = input.value.trim();
        if (!text) {
          ui.railBody.querySelectorAll(".concepts-rail-results").forEach((node) => node.remove());
          return;
        }
        api(`/api/library/concepts?q=${encodeURIComponent(text)}`).then((payload) => {
          ui.railBody.querySelectorAll(".concepts-rail-results").forEach((node) => node.remove());
          const box = el("div", "concepts-rail-results");
          (payload.concepts || []).forEach((concept) => {
            const button = el("button", "concepts-rail-item", concept.name);
            button.type = "button";
            if (concept.ambiguous) {
              // 同名但含义可能不同：不默认挑一个，把范围说明摆出来。
              button.classList.add("is-ambiguous");
              button.append(el("span", "concepts-rail-note", concept.scope_note || "同名，需你确认含义"));
            }
            button.addEventListener("click", () => {
              state.query = emptyQuery();
              state.query.concept_groups.push({
                ids: [concept.id],
                roles: ["primary"],
                all_or_any: "all",
                include_descendants: false,
                names: [concept.name],
              });
              renderChips();
              runSearch();
            });
            box.append(button);
          });
          if (!(payload.concepts || []).length) {
            box.append(el("p", "concepts-hint", "目录里没有这个概念。"));
          }
          ui.railBody.append(box);
        });
      });
      ui.railBody.append(input);
      return;
    }

    const note = el(
      "p",
      "concepts-hint",
      "个人专题是老师自己组织的用法，和教材目录、数学概念各自独立。一道题可以进两个专题。",
    );
    ui.railBody.append(note);
    const collections = state.railData.collections || [];
    if (!collections.length) {
      ui.railBody.append(el("p", "concepts-hint", "还没有专题。"));
      return;
    }
    collections.forEach((collection) => {
      const button = el(
        "button",
        "concepts-rail-item",
        `${collection.name}（${collection.member_count}）`,
      );
      button.type = "button";
      button.addEventListener("click", () => {
        state.query = emptyQuery();
        state.query.filters.collection = collection.id;
        renderChips();
        runSearch();
      });
      ui.railBody.append(button);
    });
  }

  // ---------------------------------------------------------------- 条件条

  function renderChips() {
    if (!ui.chips) return;
    ui.chips.replaceChildren();
    const chips = conditionChips(state.query);
    if (!chips.length) {
      ui.chips.append(el("span", "concepts-hint", "还没有条件 —— 直接搜题面，或者从左边挑一个入口。"));
      return;
    }
    chips.forEach((chip) => {
      const node = el("span", "concepts-chip");
      node.append(el("span", "concepts-chip-text", chip.text));
      const remove = el("button", "concepts-chip-remove", "×");
      remove.type = "button";
      remove.title = "去掉这个条件";
      remove.addEventListener("click", () => {
        state.query = removeCondition(state.query, chip.remove);
        renderChips();
        runSearch();
      });
      node.append(remove);
      ui.chips.append(node);
    });
    const clear = el("button", "btn btn-quiet", "清空条件");
    clear.type = "button";
    clear.addEventListener("click", () => {
      state.query = emptyQuery();
      renderChips();
      runSearch();
    });
    ui.chips.append(clear);
  }

  // ---------------------------------------------------------------- 检索

  async function runSearch() {
    const text = (ui.search.value || "").trim();
    if (text && !state.query.keywords.length && !state.query.concept_groups.length) {
      // 一句话进来：先做本地转换，转换结果摆出来给老师看，可以改。
      try {
        const conversion = await api("/api/library/search", {
          method: "POST",
          body: { action: "convert", text },
        });
        state.conversion = conversion;
        state.query = conversion.query;
        renderConversion();
      } catch (error) {
        state.message = error.message;
      }
    }
    try {
      const result = await api("/api/library/search", {
        method: "POST",
        body: { query: state.query, limit: 200 },
      });
      state.results = result;
      state.message = "";
    } catch (error) {
      state.message = error.message;
      state.results = null;
    }
    renderChips();
    renderSummary();
    renderResults();
  }

  function renderConversion() {
    if (!state.conversion) return;
    const notes = state.conversion.notes || [];
    if (!notes.length) return;
    const box = el("div", "concepts-conversion");
    box.append(el("span", "concepts-conversion-title", "这句话被理解成了："));
    notes.forEach((note) => {
      box.append(el("span", "concepts-conversion-item", `${note.source} → ${note.meaning}`));
    });
    box.append(
      el("span", "concepts-conversion-note", "认不出来的部分放在关键词里，没当成正式条件。条件可以逐条去掉。"),
    );
    const summary = ui.summary;
    if (summary && !summary.querySelector(".concepts-conversion")) {
      summary.prepend(box);
    } else if (summary) {
      summary.replaceChildren(box);
    }
  }

  function renderSummary() {
    if (!ui.summary) return;
    const result = state.results;
    if (!result) {
      if (state.message) {
        ui.summary.replaceChildren(el("p", "concepts-hint", state.message));
      }
      return;
    }
    const nodes = [];
    nodes.push(el("span", "concepts-count", `已确认匹配 ${result.confirmed_count ?? 0} 题`));
    if (result.legacy_count) {
      nodes.push(el("span", "concepts-count is-legacy", `旧标签命中 ${result.legacy_count} 题（未定位具体解法）`));
    }
    if (result.clue_count) {
      nodes.push(el("span", "concepts-count is-clue", `待核对线索 ${result.clue_count} 题`));
    }
    if (result.unknown_count) {
      nodes.push(el("span", "concepts-count is-unknown", `未整理 ${result.unknown_count} 题`));
    }
    if (result.excluded_count) {
      nodes.push(el("span", "concepts-count is-excluded", `按排除条件去掉 ${result.excluded_count} 题`));
    }
    const conversion = state.conversion ? state.conversion.notes || [] : [];
    ui.summary.replaceChildren(...nodes);
    if (conversion.length) {
      const box = el("div", "concepts-conversion");
      box.append(el("span", "concepts-conversion-title", "这句话被理解成了："));
      conversion.forEach((note) => {
        box.append(el("span", "concepts-conversion-item", `${note.source} → ${note.meaning}`));
      });
      box.append(el("span", "concepts-conversion-note", "认不出来的部分放在关键词里，没当成正式条件。"));
      ui.summary.append(box);
    }
  }

  function renderResults() {
    if (!ui.list) return;
    ui.list.replaceChildren();
    const result = state.results;
    if (!result) return;
    const parts = partitionResults(result);
    const groups = [
      { key: "confirmed", title: "已确认匹配", items: parts.confirmed, note: "人工确认过，依据当前有效。" },
      { key: "legacy", title: "旧标签命中", items: parts.legacy, note: "旧记录，没定位到具体解法，不冒充精确的方法证明。" },
      { key: "clue", title: "待核对线索", items: parts.clues, note: "还没核对，不计入正式匹配。" },
      {
        key: "unreviewed",
        title: "未整理",
        items: parts.unreviewed,
        note: "这些题还没贴过标签。没贴标签不等于没有该知识或方法。",
      },
    ];
    let shown = 0;
    groups.forEach((group) => {
      if (!group.items.length) return;
      const section = el("section", "concepts-group");
      const heading = el("h2", "concepts-group-title");
      heading.append(el("span", "", group.title));
      heading.append(el("span", "concepts-group-count", String(group.items.length)));
      section.append(heading);
      section.append(el("p", "concepts-group-note", group.note));
      group.items.slice(0, Math.max(0, LIST_LIMIT - shown)).forEach((item) => {
        section.append(resultCard(item, group.key));
        shown += 1;
      });
      ui.list.append(section);
    });
    if (shown > LIST_LIMIT) {
      ui.list.append(el("p", "concepts-hint", `只显示前 ${LIST_LIMIT} 题。`));
    }
    if (!shown) {
      ui.list.append(
        el("p", "concepts-hint", "没找到题。可能是条件太窄，也可能是这些题还没整理过 —— 后者不是「没有」。"),
      );
    }
  }

  function resultCard(item, bucket) {
    const card = el("article", "concepts-card");
    if (bucket !== "confirmed") card.classList.add("is-" + bucket);
    const head = el("div", "concepts-card-head");
    head.append(el("span", "concepts-card-number", `第 ${item.number} 题`));
    head.append(el("span", "concepts-card-type", TYPE_LABELS[item.question_type] || item.question_type || ""));
    if (bucket === "legacy") {
      // 不可用的提前标出来：别让人点了才发现点错了。
      head.append(el("span", "concepts-card-flag", "旧标签"));
    }
    if (bucket === "clue") {
      head.append(el("span", "concepts-card-flag", "待核对"));
    }
    if (bucket === "unreviewed") {
      head.append(el("span", "concepts-card-flag", "未整理"));
    }
    card.append(head);
    const reasons = (item.reasons || []).map((reason) => reason.text).join("；");
    if (reasons) card.append(el("p", "concepts-card-reason", reasons));
    // 卡片本身就是入口 —— 不必先瞄一个按钮才知道能点。
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.title = "点开整理这道题";
    const open = () => selectQuestion(item.publication_id);
    card.addEventListener("click", open);
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
    return card;
  }

  // ---------------------------------------------------------------- 当前题

  async function selectQuestion(publicationId) {
    state.selected = publicationId;
    const [detail, assertions] = await Promise.all([
      api(`/api/library/${publicationId}`),
      api(`/api/library/${publicationId}/assertions`),
    ]);
    state.detail = detail;
    state.detail.assertions = assertions.assertions;
    state.detail.coverage = assertions.coverage;
    state.detail.subquestions = assertions.subquestions;
    state.detail.variants = assertions.variants;
    // 默认落在第一个解法分支上 —— 不然「记录采用的方法」一直灰着，
    // 老师要先想一遍该选哪个才用得上。
    const variants = assertions.variants || [];
    state.scope = { subquestion_id: "", variant_id: variants.length ? variants[0].id : "" };
    renderInspector();
  }

  function renderInspector() {
    if (!ui.inspector) return;
    ui.inspector.replaceChildren();
    if (!state.detail) {
      ui.inspector.append(el("p", "concepts-hint", "左边点一道题，这里就能整理。"));
      return;
    }
    const question = state.detail.publication || state.detail;
    const content = question.content || {};

    const title = el("h2", "concepts-inspector-title", `第 ${question.number} 题`);
    ui.inspector.append(title);
    const stem = el("div", "concepts-inspector-stem");
    if (root.QBRender && root.QBRender.renderQuestion) {
      try {
        stem.append(root.QBRender.renderQuestion(content));
      } catch (error) {
        stem.textContent = content.stem || "";
      }
    } else {
      stem.textContent = content.stem || "";
    }
    ui.inspector.append(stem);

    // ---- 撤销 ----------------------------------------------------------
    // 撤销是真正的逆操作，后端带冲突检查：那条关联要是被别人改过，
    // 撤销会拒绝并说清楚，不会盖掉别人的改动。
    const undoable = (state.detail.operations || []).find((item) => !item.undone);
    if (undoable) {
      const undo = el("button", "concepts-undo", "撤销上次改动");
      undo.type = "button";
      undo.title = undoable.action === "attach_assertion" ? "撤销刚贴的标签" : "撤销刚才的移除";
      undo.addEventListener("click", () => undoAssertion(undoable.id));
      ui.inspector.append(undo);
    }

    // ---- 主要考查 ------------------------------------------------------
    ui.inspector.append(section("主要考查", (body) => {
      listRole(body, "primary");
      addConceptButton(body, "primary");
    }));

    // ---- 当前解法 ------------------------------------------------------
    ui.inspector.append(section("当前解法", (body) => {
      const scopeRow = el("div", "concepts-field-row");
      scopeRow.append(el("span", "concepts-field-label", "范围"));
      const whole = el("button", "concepts-scope is-active", "整题");
      whole.type = "button";
      const subButtons = [];
      (state.detail.subquestions || []).forEach((sub) => {
        const button = el("button", "concepts-scope", sub.display_label || sub.stable_key);
        button.type = "button";
        button.addEventListener("click", () => {
          whole.classList.remove("is-active");
          button.classList.add("is-active");
          subButtons.forEach((other) => other.classList.remove("is-active"));
          state.scope = { subquestion_id: sub.id, variant_id: state.scope?.variant_id || "" };
        });
        subButtons.push(button);
        scopeRow.append(button);
      });
      whole.addEventListener("click", () => {
        whole.classList.add("is-active");
        subButtons.forEach((other) => other.classList.remove("is-active"));
        state.scope = { subquestion_id: "", variant_id: state.scope?.variant_id || "" };
      });
      body.append(scopeRow);

      const variantRow = el("div", "concepts-field-row");
      variantRow.append(el("span", "concepts-field-label", "解法"));
      const variants = (state.detail.variants || []).filter((variant) => variant.status === "active");
      const variantButtons = [];
      variants.forEach((variant) => {
        const button = el("button", "concepts-scope", variant.name);
        button.type = "button";
        if (state.scope?.variant_id === variant.id) button.classList.add("is-active");
        button.addEventListener("click", () => {
          variantButtons.forEach((other) => other.classList.remove("is-active"));
          button.classList.add("is-active");
          state.scope = { ...(state.scope || {}), variant_id: variant.id };
        });
        variantButtons.push(button);
        variantRow.append(button);
      });
      if (!variants.length) {
        variantRow.append(el("span", "concepts-hint", "还没有解法分支。"));
      }
      body.append(variantRow);

      const add = addConceptButton(body, "method");
      // 灰控件旁边必须有看得见的一行说明：差哪一步、去哪做。
      if (!state.scope?.variant_id) {
        add.classList.add("is-disabled");
        add.disabled = true;
        body.append(el("p", "concepts-hint", "「解法采用」要先选一个解法分支 —— 没有分支就记不了是哪种解法。"));
      }
      listRole(body, "method");
    }));

    // ---- 其他已记录解法 ------------------------------------------------
    ui.inspector.append(section("其他已记录解法", (body) => {
      const others = (state.detail.variants || []).filter((variant) => variant.status !== "active");
      if (!others.length) {
        body.append(
          el("p", "concepts-hint", "还没记录别的解法。这里只放你已经确认过的，不自动生成 —— 「已记录」不等于「全部」。"),
        );
        return;
      }
      others.forEach((variant) => {
        const row = el("div", "concepts-variant");
        row.append(el("span", "concepts-variant-name", variant.name));
        const methods = (state.detail.assertions || [])
          .filter((item) => item.variant_id === variant.id)
          .map((item) => item.tag_name);
        row.append(el("span", "concepts-assertion-scope", methods.length ? methods.join("、") : "还没记方法"));
        body.append(row);
      });
    }));

    // ---- 更多信息 ------------------------------------------------------
    ui.inspector.append(section("更多信息", (body) => {
      listRole(body, "supporting");
      listRole(body, "feature");
      const coverage = state.detail.coverage || {};
      const statuses = coverage.coverage || [];
      if (!statuses.length) {
        body.append(el("p", "concepts-hint", "这道题还没标整理程度。「没贴标签」不等于「没有」。"));
      } else {
        statuses.forEach((item) => {
          body.append(el("p", "concepts-hint", `${item.scope_key} · ${item.status}`));
        });
      }
    }));

    // ---- 待核对建议 ----------------------------------------------------
    ui.inspector.append(section("待核对建议", (body) => {
      const suggestion = (state.detail.publication && state.detail.publication.tag_suggestion) || state.detail.tag_suggestion;
      if (!suggestion || suggestion.status !== "pending") {
        body.append(el("p", "concepts-hint", "没有待核对的建议。生成建议要你自己点，不会自动跑。"));
        return;
      }
      const entries = suggestion.suggestions || [];
      if (!entries.length) {
        body.append(el("p", "concepts-hint", "这条候选还没有内容。"));
        return;
      }
      entries.forEach((entry, index) => {
        const box = el("div", "concepts-suggestion");
        box.append(el("div", "concepts-suggestion-name", entry.name || entry.new_name || "（未命名）"));
        box.append(el("div", "concepts-suggestion-role", `拟议用途：${ROLE_LABELS[entry.role] || entry.role || "主要考查"}`));
        if (entry.rationale) box.append(el("p", "concepts-suggestion-why", entry.rationale));
        if (entry.evidence) {
          const details = el("details", "concepts-suggestion-evidence");
          details.append(el("summary", "", "查看依据"));
          details.append(el("p", "", entry.evidence));
          box.append(details);
        }
        const actions = el("div", "concepts-suggestion-actions");
        const accept = el("button", "btn btn-quiet", "采纳");
        accept.type = "button";
        accept.addEventListener("click", () => resolveSuggestion(suggestion.id, "accept", index));
        const reject = el("button", "btn btn-quiet", "拒绝");
        reject.type = "button";
        reject.addEventListener("click", () => resolveSuggestion(suggestion.id, "reject", index));
        actions.append(accept, reject);
        box.append(actions);
        body.append(box);
      });
    }));
  }

  function section(title, fill) {
    const box = el("section", "concepts-inspector-section");
    box.append(el("h3", "concepts-inspector-section-title", title));
    const body = el("div", "concepts-inspector-section-body");
    fill(body);
    box.append(body);
    return box;
  }

  function listRole(body, role) {
    const rows = (state.detail.assertions || []).filter((item) => item.role === role);
    rows.forEach((row) => {
      const node = el("div", "concepts-assertion");
      node.append(el("span", "concepts-assertion-name", row.tag_name));
      // 范围要说清楚是整题、哪一小问、哪个解法 —— 光写「整题」会骗人：
      // 两个方法分属两个解法时，两条都写「整题」就分不出来了。
      const parts = [];
      if (!row.whole) parts.push(row.subquestion_label || "某小问");
      if (row.variant_name) parts.push(row.variant_name);
      node.append(el("span", "concepts-assertion-scope", parts.length ? parts.join(" · ") : "整题"));
      if (row.evidence_status === "unbound") {
        node.append(el("span", "concepts-assertion-flag", "旧记录未绑定具体依据"));
      }
      const remove = el("button", "concepts-assertion-remove", "×");
      remove.type = "button";
      remove.title = "移除这条关联";
      remove.addEventListener("click", () => detachAssertion(row));
      node.append(remove);
      body.append(node);
    });
  }

  function addConceptButton(body, role) {
    const button = el("button", "concepts-add", "＋添加");
    button.type = "button";
    button.addEventListener("click", () => openConceptPicker(role));
    body.append(button);
    return button;
  }

  function openConceptPicker(role) {
    const input = el("input", "concepts-picker-input");
    input.type = "search";
    input.placeholder = "输入概念名或别名，回车选择已有的";
    const list = el("div", "concepts-picker-list");
    const box = el("div", "concepts-picker");
    box.append(input, list);
    const close = () => box.remove();
    input.addEventListener("input", async () => {
      const text = input.value.trim();
      list.replaceChildren();
      if (!text) return;
      const payload = await api(`/api/library/concepts?q=${encodeURIComponent(text)}`);
      (payload.concepts || []).forEach((concept) => {
        const option = el("button", "concepts-picker-option");
        option.type = "button";
        option.append(el("span", "", concept.name));
        if (concept.scope_note) option.append(el("span", "concepts-picker-note", concept.scope_note));
        if (concept.ambiguous) option.append(el("span", "concepts-picker-flag", "同名，看范围说明"));
        option.addEventListener("click", async () => {
          await attachAssertion(concept, role);
          close();
        });
        list.append(option);
      });
      if (!(payload.concepts || []).length) {
        list.append(el("p", "concepts-hint", "目录里没有这个概念。"));
      }
    });
    document.body.append(box);
    input.focus();
  }

  async function attachAssertion(concept, role) {
    const payload = {
      action: "attach",
      publication_id: state.selected,
      tag_ids: [concept.id],
      role,
      client_request_id: newRequestId(),
      expected_revision: state.detail.tag_revision || 0,
      scope_key: "whole",
    };
    if (role === "method" && state.scope?.variant_id) payload.variant_id = state.scope.variant_id;
    if (role === "method" && state.scope?.subquestion_id) payload.subquestion_id = state.scope.subquestion_id;
    try {
      await api("/api/library/assertions", { method: "POST", body: payload });
      await selectQuestion(state.selected);
    } catch (error) {
      state.message = error.message;
      renderSummary();
    }
  }

  async function detachAssertion(row) {
    try {
      await api("/api/library/assertions", {
        method: "POST",
        body: {
          action: "detach",
          assignment_id: row.id,
          client_request_id: newRequestId(),
          expected_revision: state.detail.tag_revision || 0,
        },
      });
      await selectQuestion(state.selected);
    } catch (error) {
      state.message = error.message;
      renderSummary();
    }
  }

  async function undoAssertion(operationId) {
    try {
      await api("/api/library/assertions", {
        method: "POST",
        body: {
          action: "undo",
          operation_id: operationId,
          client_request_id: newRequestId(),
          expected_revision: state.detail.tag_revision || 0,
        },
      });
      await selectQuestion(state.selected);
    } catch (error) {
      state.message = error.message;
      renderSummary();
    }
  }

  async function resolveSuggestion(suggestionId, action, index) {
    try {
      await api(`/api/library/tag-suggestions/${suggestionId}/resolve`, {
        method: "POST",
        body: {
          action,
          indices: [index],
          request_id: newRequestId(),
          expected_revision: state.detail.tag_revision || 0,
        },
      });
      await selectQuestion(state.selected);
    } catch (error) {
      state.message = error.message;
      renderSummary();
    }
  }

  // ---------------------------------------------------------------- 保存搜索

  async function saveCurrentSearch() {
    const name = window.prompt("给这次搜索起个名字", "我的找题");
    if (!name) return;
    try {
      const payload = await api("/api/library/saved-searches", {
        method: "POST",
        body: { action: "save", name, query: state.query },
      });
      state.savedSearches = payload.saved_searches || [];
      renderSavedSearches();
    } catch (error) {
      state.message = error.message;
      renderSummary();
    }
  }

  async function loadSavedSearches() {
    const payload = await api("/api/library/saved-searches");
    state.savedSearches = payload.saved_searches || [];
    renderSavedSearches();
  }

  function renderSavedSearches() {
    if (!ui.savedSelect) return;
    ui.savedSelect.replaceChildren();
    const blank = el("option", "", "已保存的搜索");
    blank.value = "";
    ui.savedSelect.append(blank);
    (state.savedSearches || []).forEach((item) => {
      const option = el("option", "", item.name);
      option.value = item.id;
      ui.savedSelect.append(option);
    });
  }

  // ---------------------------------------------------------------- 启动

  async function boot() {
    bindUi();
    renderChips();
    await Promise.all([loadRail(), loadSavedSearches()]);
    await runSearch();
  }

  root.QBConcepts = Object.freeze({
    conditionChips,
    partitionResults,
    removeCondition,
    emptyQuery,
    state,
  });

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", boot);
    } else {
      boot();
    }
  }
})();
