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
    edits: {},
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
      const create = el("button", "concepts-rail-create", "＋新建章节");
      create.type = "button";
      create.addEventListener("click", () => openChapterForm());
      ui.railBody.append(create);
      const textbooks = state.railData.textbooks || [];
      const all = state.railData.chapters || [];
      // 「通用教学目录」不是一本教材，它是不指定版本时章节的落脚处。
      // 它不在教材表里，但章节可能属于它 —— 所以要单独列一组，
      // 不然在新库上建的章节会一个都看不见。
      const generic = all.filter((chapter) => !chapter.textbook_id);
      if (!textbooks.length && !generic.length) {
        ui.railBody.append(el("p", "concepts-hint", "还没有教材目录。"));
        return;
      }
      const groups = textbooks.map((textbook) => ({
        id: textbook.id,
        name: textbook.name,
        chapters: all.filter((chapter) => chapter.textbook_id === textbook.id),
      }));
      if (generic.length) {
        groups.push({ id: "", name: "通用教学目录", chapters: generic });
      }
      groups.forEach((group) => {
        const block = el("section", "concepts-rail-block");
        block.append(el("h3", "concepts-rail-sub", group.name));
        if (!group.chapters.length) {
          block.append(el("p", "concepts-hint", "这一本还没有章节。"));
        }
        // 章 → 节。目录本来就是有层级的，平着摆看不出哪节属于哪章。
        chapterRows(group.chapters, "").forEach((row) => {
          const button = el("button", "concepts-rail-item", row.chapter.title);
          button.type = "button";
          button.style.paddingLeft = `${6 + row.depth * 14}px`;
          if (row.depth > 0) button.classList.add("is-child");
          button.addEventListener("click", () => {
            state.query = emptyQuery();
            state.query.filters.chapter = row.chapter.id;
            if (group.id) state.query.filters.textbook = group.id;
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
            const maintain = el("button", "concepts-rail-maintain", "维护");
            maintain.type = "button";
            maintain.title = "改名字、别名、范围说明；停用；或合并到别的概念";
            maintain.addEventListener("click", (event) => {
              event.stopPropagation();
              openConceptMaintenance(concept);
            });
            const row = el("div", "concepts-rail-item-row");
            row.append(button, maintain);
            box.append(row);
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
    const create = el("button", "concepts-rail-create", "＋新建专题");
    create.type = "button";
    create.addEventListener("click", () => createCollection());
    ui.railBody.append(create);
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
    // 空条件时后端不细分桶（没逐题查关联），只报「一共多少题」。
    if (result.confirmed_count === null || result.confirmed_count === undefined) {
      nodes.push(el("span", "concepts-count", `共 ${result.total_count ?? result.count} 题`));
    } else {
      nodes.push(el("span", "concepts-count", `已确认匹配 ${result.confirmed_count} 题`));
    }
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

  /** 章节按父子层级排好：章在前，节跟在它下面。 */
  function chapterRows(chapters, parentId, depth = 0) {
    const rows = [];
    chapters
      .filter((chapter) => (chapter.parent_id || "") === (parentId || ""))
      .forEach((chapter) => {
        rows.push({ chapter, depth });
        rows.push(...chapterRows(chapters, chapter.id, depth + 1));
      });
    return rows;
  }

  /** 新建一节：可以选放在哪本教材下、属于哪一章。 */
  function openChapterForm() {
    const form = el("div", "concepts-chapter-form");
    const titleInput = el("input", "concepts-field-input");
    titleInput.type = "text";
    titleInput.placeholder = "比如「第十六章 二次方程」";
    form.append(el("span", "concepts-field-label", "这一节叫什么"), titleInput);

    const textbookSelect = el("select", "concepts-field-input");
    const generic = el("option", "", "通用教学目录（版本未指定）");
    generic.value = "";
    textbookSelect.append(generic);
    (state.railData.textbooks || []).forEach((textbook) => {
      const option = el("option", "", textbook.name);
      option.value = textbook.id;
      textbookSelect.append(option);
    });
    form.append(el("span", "concepts-field-label", "放在哪本教材下"), textbookSelect);

    const parentSelect = el("select", "concepts-field-input");
    const noParent = el("option", "", "（作为最上面的一层）");
    noParent.value = "";
    parentSelect.append(noParent);
    const refreshParents = () => {
      parentSelect.replaceChildren(noParent);
      const textbookId = textbookSelect.value;
      (state.railData.chapters || [])
        .filter((chapter) => (chapter.textbook_id || "") === (textbookId || ""))
        .forEach((chapter) => {
          const option = el("option", "", `在「${chapter.title}」下面`);
          option.value = chapter.id;
          parentSelect.append(option);
        });
    };
    textbookSelect.addEventListener("change", refreshParents);
    refreshParents();
    form.append(el("span", "concepts-field-label", "属于哪一章"), parentSelect);

    const status = el("p", "concepts-field-hint");
    const actions = el("div", "concepts-maintain-actions");
    const save = el("button", "btn", "建好");
    save.type = "button";
    save.addEventListener("click", async () => {
      const title = titleInput.value.trim();
      if (!title) {
        status.textContent = "先给这一节起个名字。";
        return;
      }
      try {
        await api("/api/library/chapters", {
          method: "POST",
          body: {
            title,
            textbook_id: textbookSelect.value,
            parent_id: parentSelect.value,
          },
        });
        await loadRail();
      } catch (error) {
        status.textContent = error.message;
      }
    });
    const cancel = el("button", "btn btn-quiet", "算了");
    cancel.type = "button";
    cancel.addEventListener("click", () => form.remove());
    actions.append(save, cancel);
    form.append(actions, status);
    ui.railBody.insertBefore(form, ui.railBody.children[2] || null);
    titleInput.focus();
  }

  async function createCollection() {
    const name = window.prompt("专题叫什么？（比如「周五小测」「易漏解」）");
    if (!name || !name.trim()) return;
    try {
      await api("/api/library/collections", {
        method: "POST",
        body: { action: "create", name: name.trim() },
      });
      await loadRail();
    } catch (error) {
      state.message = error.message;
      renderSummary();
    }
  }

  async function toggleCollection(collectionId, member) {
    try {
      await api("/api/library/collections", {
        method: "POST",
        body: {
          action: member ? "remove" : "add",
          collection_id: collectionId,
          publication_id: state.selected,
        },
      });
      await selectQuestion(state.selected);
      await loadRail();
    } catch (error) {
      state.message = error.message;
      renderSummary();
    }
  }

  async function attachChapter(chapterId) {
    try {
      await api("/api/library/placements", {
        method: "POST",
        body: {
          action: "attach",
          publication_id: state.selected,
          chapter_id: chapterId,
          relation: "teaching",
        },
      });
      await selectQuestion(state.selected);
    } catch (error) {
      state.message = error.message;
      renderSummary();
    }
  }

  // ---------------------------------------------------------------- 旧标签组合转换

  const LEGACY_TAG_FILTERS_KEY = "qb-library-saved-tag-filters";

  function readLegacyFilters() {
    try {
      const raw = window.localStorage.getItem(LEGACY_TAG_FILTERS_KEY);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.filter((item) => item && item.name) : [];
    } catch (error) {
      return [];
    }
  }

  /** 旧组合里存的是标签**名字**，新搜索要的是稳定 id —— 名字得一个一个换成 id。 */
  async function resolveTagIds(names) {
    const found = [];
    for (const name of names) {
      const text = String(name || "").trim();
      if (!text) continue;
      const payload = await api(`/api/library/concepts?q=${encodeURIComponent(text)}`);
      const hits = payload.concepts || [];
      // 先找完全同名的，找不到再退回第一个 —— 同名但含义可能不同，所以先精确匹配。
      const hit = hits.find((item) => item.name === text) || hits[0];
      if (hit) found.push({ id: hit.id, name: hit.name, kind: hit.kind });
    }
    return found;
  }

  function legacyToQuery(item, resolved) {
    const filter = item.filter || {};
    const mode = String(filter.mode || "all") === "any" ? "any" : "all";
    const all = resolved.filter((row) => (filter.all || []).includes(row.name));
    const any = resolved.filter((row) => (filter.any || []).includes(row.name));
    const not = resolved.filter((row) => (filter.not || []).includes(row.name));
    const conceptIds = (mode === "any" ? any : all).filter((row) => row.kind !== "method");
    const methodIds = (mode === "any" ? any : all).filter((row) => row.kind === "method");
    const query = {
      query_version: 2,
      keywords: [],
      filters: {},
      concept_groups: conceptIds.length
        ? [{ ids: conceptIds.map((row) => row.id), roles: ["primary"], all_or_any: "all", include_descendants: false }]
        : [],
      method_groups: methodIds.length
        ? [{ ids: methodIds.map((row) => row.id), subquestion: "", variant_scope: "", cooccurrence: "same_variant" }]
        : [],
      exclusions: {
        conditions: not.length
          ? [{ ids: not.map((row) => row.id), roles: ["primary", "method"], include_descendants: false }]
          : [],
        handling_of_unknown: "separate",
      },
      include_clues: false,
      sort: "recent",
    };
    if (mode === "any" && query.concept_groups.length) {
      query.concept_groups[0].all_or_any = "any";
    }
    return query;
  }

  async function offerLegacyConversion() {
    const legacy = readLegacyFilters();
    const banner = document.getElementById("conceptsLegacyBanner");
    if (!banner) return;
    if (!legacy.length) {
      banner.hidden = true;
      return;
    }
    const existing = new Set((state.savedSearches || []).map((item) => item.name));
    const pending = legacy.filter((item) => !existing.has(String(item.name || "").trim()));
    if (!pending.length) {
      banner.hidden = true;
      return;
    }
    banner.hidden = false;
    banner.replaceChildren();
    banner.append(
      el(
        "span",
        "concepts-legacy-text",
        `你以前在浏览器里存了 ${pending.length} 条「保存标签组合」。要转成现在的完整搜索吗？转完旧的还留着，不影响你。`,
      ),
    );
    const doIt = el("button", "btn btn-quiet", "转成新搜索");
    doIt.type = "button";
    doIt.addEventListener("click", () => convertLegacyFilters(pending));
    banner.append(doIt);
    const skip = el("button", "btn btn-quiet", "先不转");
    skip.type = "button";
    skip.addEventListener("click", () => {
      banner.hidden = true;
    });
    banner.append(skip);
  }

  async function convertLegacyFilters(pending) {
    const banner = document.getElementById("conceptsLegacyBanner");
    let done = 0;
    let skipped = 0;
    for (const item of pending) {
      const names = [
        ...((item.filter || {}).all || []),
        ...((item.filter || {}).any || []),
        ...((item.filter || {}).not || []),
      ];
      const resolved = await resolveTagIds(names);
      const query = legacyToQuery(item, resolved);
      if (!query.concept_groups.length && !query.method_groups.length) {
        skipped += 1;
        continue;
      }
      try {
        await api("/api/library/saved-searches", {
          method: "POST",
          body: { action: "save", name: String(item.name).trim(), query },
        });
        done += 1;
      } catch (error) {
        skipped += 1;
      }
    }
    await loadSavedSearches();
    if (banner) {
      banner.hidden = false;
      banner.replaceChildren();
      banner.append(
        el(
          "span",
          "concepts-legacy-text",
          `转好了 ${done} 条${skipped ? `，${skipped} 条没转（里面的标签名在目录里找不到了）` : ""}。旧的那几条还在浏览器里。`,
        ),
      );
      const close = el("button", "btn btn-quiet", "知道了");
      close.type = "button";
      close.addEventListener("click", () => {
        banner.hidden = true;
      });
      banner.append(close);
    }
  }

  // ---------------------------------------------------------------- 概念维护

  const TYPE_HINT_OPTIONS = [
    ["知识点", "knowledge"],
    ["解题方法", "method"],
    ["自定义", "custom"],
  ];

  async function catalogueRevision() {
    const payload = await api("/api/library/tags");
    return { revision: payload.revision, tags: payload.tags || [] };
  }

  async function catalogueMutation(payload) {
    const { revision } = await catalogueRevision();
    return api("/api/library/tags", {
      method: "POST",
      body: { ...payload, revision, request_id: newRequestId() },
    });
  }

  function field(label, value, hint) {
    const wrap = el("label", "concepts-field");
    wrap.append(el("span", "concepts-field-label", label));
    const input = el("input", "concepts-field-input");
    input.type = "text";
    input.value = value ?? "";
    wrap.append(input);
    if (hint) wrap.append(el("span", "concepts-field-hint", hint));
    return { wrap, input };
  }

  function openConceptMaintenance(tag) {
    const overlay = el("div", "concepts-overlay");
    const panel = el("div", "concepts-maintain");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", `维护概念 ${tag.name}`);

    const head = el("div", "concepts-maintain-head");
    head.append(el("h2", "concepts-maintain-title", tag.name));
    const close = el("button", "concepts-maintain-close", "×");
    close.type = "button";
    close.title = "关闭";
    close.addEventListener("click", () => overlay.remove());
    head.append(close);
    panel.append(head);

    const nameField = field("规范名", tag.name, "改了名，旧名会自动收进别名，检索还找得到。");
    const aliasField = field("别名", (tag.aliases || []).join("，"), "用逗号分开。");
    const scopeField = field(
      "范围说明",
      tag.scope_note || "",
      "同名但含义不同时写一句区分 —— 界面会把它显示出来，不会自动合并。",
    );
    const descField = field("定义", tag.description || "", "这个概念是什么。");

    // 类型提示是多值的：同一个概念可以既是考点又是手段。
    const hintWrap = el("div", "concepts-field");
    hintWrap.append(el("span", "concepts-field-label", "类型提示"));
    const hintBoxes = [];
    TYPE_HINT_OPTIONS.forEach(([label, value]) => {
      const row = el("label", "concepts-hint-check");
      const box = el("input");
      box.type = "checkbox";
      box.checked = (tag.type_hints || []).includes(value);
      row.append(box, el("span", "", label));
      hintWrap.append(row);
      hintBoxes.push([box, value]);
    });
    hintWrap.append(el("span", "concepts-field-hint", "可以多选。类型提示只影响推荐和浏览，不决定这个概念的身份。"));

    panel.append(nameField.wrap, aliasField.wrap, scopeField.wrap, descField.wrap, hintWrap);

    const status = el("p", "concepts-maintain-status");
    panel.append(status);

    const actions = el("div", "concepts-maintain-actions");
    const save = el("button", "btn", "保存");
    save.type = "button";
    save.addEventListener("click", async () => {
      const hints = hintBoxes.filter(([box]) => box.checked).map(([, value]) => value);
      try {
        await catalogueMutation({
          action: "update_tag",
          tag_id: tag.id,
          name: nameField.input.value.trim(),
          aliases: aliasField.input.value.split(/[，,]/).map((item) => item.trim()).filter(Boolean),
          scope_note: scopeField.input.value.trim(),
          description: descField.input.value.trim(),
          type_hints: hints,
        });
        status.textContent = "存好了。";
        await loadRail();
        await offerLegacyConversion();
      } catch (error) {
        status.textContent = error.message;
      }
    });
    actions.append(save);

    const toggle = el("button", "btn btn-quiet", tag.active ? "停用" : "启用");
    toggle.type = "button";
    toggle.addEventListener("click", async () => {
      try {
        await catalogueMutation({
          action: tag.active ? "deactivate_tag" : "activate_tag",
          tag_id: tag.id,
        });
        status.textContent = tag.active ? "停用了。停用不是删除，历史还在。" : "启用好了。";
        await loadRail();
      } catch (error) {
        status.textContent = error.message;
      }
    });
    actions.append(toggle);

    const merge = el("button", "btn btn-quiet", "合并到别的概念…");
    merge.type = "button";
    merge.addEventListener("click", () => openMergeFlow(tag, status, overlay));
    actions.append(merge);

    panel.append(actions);
    overlay.append(panel);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) overlay.remove();
    });
    document.body.append(overlay);
  }

  async function openMergeFlow(sourceTag, status, overlay) {
    const box = el("div", "concepts-merge");
    box.append(el("h3", "concepts-merge-title", `把「${sourceTag.name}」合并到`));
    const input = el("input", "concepts-field-input");
    input.type = "search";
    input.placeholder = "输入要合并进去的那个概念…";
    box.append(input);
    const results = el("div", "concepts-merge-results");
    box.append(results);
    const note = el("p", "concepts-field-hint", "合并会把这颗标签下的题和别名都转到目标概念上，旧的名字保留成别名。先看影响范围再确认。");
    box.append(note);
    status.replaceChildren(box);

    input.addEventListener("input", async () => {
      const text = input.value.trim();
      results.replaceChildren();
      if (!text) return;
      const payload = await api(`/api/library/concepts?q=${encodeURIComponent(text)}`);
      (payload.concepts || [])
        .filter((item) => item.id !== sourceTag.id)
        .forEach((item) => {
          const button = el("button", "concepts-merge-option");
          button.type = "button";
          button.append(el("span", "", item.name));
          if (item.scope_note) button.append(el("span", "concepts-merge-note", item.scope_note));
          button.addEventListener("click", () => previewMerge(sourceTag, item, status));
          results.append(button);
        });
    });
  }

  async function previewMerge(sourceTag, targetTag, status) {
    const impact = await api(
      `/api/library/tags/impact?action=delete_tag&tag_id=${sourceTag.id}&target_id=${targetTag.id}`,
    );
    const box = el("div", "concepts-merge");
    box.append(el("h3", "concepts-merge-title", `合并到「${targetTag.name}」的影响`));
    const rows = [
      ["受影响的题", impact.question_count],
      ["标签关联", impact.assignment_count],
      ["上下级关系", impact.relation_count],
      ["历史记录", impact.historical_count],
    ];
    rows.forEach(([label, value]) => {
      const row = el("div", "concepts-merge-count");
      row.append(el("span", "", label));
      row.append(el("strong", "", String(value ?? 0)));
      box.append(row);
    });
    const confirm = el("button", "btn", "确认合并");
    confirm.type = "button";
    confirm.addEventListener("click", async () => {
      try {
        const { revision } = await catalogueRevision();
        await api("/api/library/tags", {
          method: "POST",
          body: {
            action: "merge_tags",
            source_id: sourceTag.id,
            target_id: targetTag.id,
            revision,
            request_id: newRequestId(),
            confirmed: true,
            impact_token: impact.token,
          },
        });
        status.textContent = "合并好了。";
        await loadRail();
      } catch (error) {
        status.textContent = error.message;
      }
    });
    box.append(confirm);
    status.replaceChildren(box);
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
    state.detail.collections = assertions.collections || [];
    state.detail.placements = assertions.placements || [];
    state.detail.operations = assertions.operations || [];
    state.detail.tag_revision = assertions.tag_revision || 0;
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

      // ---- 教材对应 ----
      // 来源位置和教学对应分开记，不互相冒充。
      body.append(el("h4", "concepts-sub-title", "教材对应"));
      const placements = state.detail.placements || [];
      if (!placements.length) {
        body.append(el("p", "concepts-hint", "还没挂到任何章节。"));
      } else {
        placements.forEach((item) => {
          const row = el("div", "concepts-placement");
          row.append(el("span", "concepts-placement-title", `${item.textbook_name} · ${item.chapter_title}`));
          row.append(
            el("span", "concepts-placement-kind", item.relation === "source" ? "来源位置" : "教学对应"),
          );
          body.append(row);
        });
      }
      const chapters = state.railData.chapters || [];
      if (chapters.length) {
        const picker = el("select", "concepts-inline-select");
        const blank = el("option", "", "挂到一节…");
        blank.value = "";
        picker.append(blank);
        chapters.forEach((chapter) => {
          const option = el("option", "", `${chapter.textbook_name} · ${chapter.title}`);
          option.value = chapter.id;
          picker.append(option);
        });
        picker.addEventListener("change", () => {
          if (picker.value) attachChapter(picker.value);
        });
        body.append(picker);
      }

      // ---- 专题 ----
      body.append(el("h4", "concepts-sub-title", "专题"));
      const collections = state.railData.collections || [];
      if (!collections.length) {
        body.append(el("p", "concepts-hint", "还没有专题。左栏「个人专题」里可以新建。"));
      } else {
        collections.forEach((collection) => {
          const row = el("div", "concepts-collection");
          const inIt = (state.detail.collections || []).some(
            (item) => item.collection_id === collection.id,
          );
          row.append(el("span", "concepts-collection-name", collection.name));
          const toggle = el("button", "concepts-collection-toggle", inIt ? "移出" : "加入");
          toggle.type = "button";
          toggle.addEventListener("click", () => toggleCollection(collection.id, inIt));
          row.append(toggle);
          body.append(row);
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
        // 用途可以改 —— 改完按改的写进去，原建议原样留着。
        const roleRow = el("div", "concepts-suggestion-role-row");
        roleRow.append(el("span", "concepts-suggestion-role-label", "用途"));
        const roleSelect = el("select", "concepts-inline-select");
        [
          ["primary", "主要考查"],
          ["supporting", "辅助用到"],
          ["method", "解法采用"],
          ["feature", "题面特征"],
        ].forEach(([value, label]) => {
          const option = el("option", "", label);
          option.value = value;
          if ((entry.role || "primary") === value) option.selected = true;
          roleSelect.append(option);
        });
        roleSelect.addEventListener("change", () => {
          state.edits[index] = { ...(state.edits[index] || {}), role: roleSelect.value };
        });
        roleRow.append(roleSelect);
        box.append(roleRow);
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
    const edits = {};
    if (action === "accept" && state.edits[index]) {
      edits[String(index)] = state.edits[index];
    }
    try {
      await api(`/api/library/tag-suggestions/${suggestionId}/resolve`, {
        method: "POST",
        body: {
          action,
          indices: [index],
          edits,
          request_id: newRequestId(),
          expected_revision: state.detail.tag_revision || 0,
        },
      });
      state.edits = {};
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
    await offerLegacyConversion();
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
