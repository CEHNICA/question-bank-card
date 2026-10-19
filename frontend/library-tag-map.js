/* A local, read-only map of the user's existing tag directory. Editing uses the manager's normal callbacks. */
((root) => {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const WIDTH = 216, HEIGHT = 96, COLUMN_GAP = 104, ROW_GAP = 24, MARGIN = 40;
  const KIND_NAMES = { knowledge: "知识点", method: "解题方法", custom: "自定义", legacy: "历史标签" };
  const normalized = (value) => String(value || "").normalize("NFKC").toLocaleLowerCase();
  const ids = (values) => [...new Set((Array.isArray(values) ? values : []).map((value) =>
    String(value && typeof value === "object" ? value.id : value)).filter((value) => value && value !== "undefined" && value !== "null"))];
  const compare = (a, b) => String(a.name).localeCompare(String(b.name), "zh-CN") || a.id.localeCompare(b.id);
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));

  /** Normalize both directions without duplicating a node that has several parents. */
  function buildModel(catalogue = {}, kind = "knowledge") {
    const allTags = new Map();
    for (const tag of catalogue.tags || []) {
      if (tag && tag.id != null) allTags.set(String(tag.id), { ...tag, id: String(tag.id), name: String(tag.name || "未命名标签") });
    }
    const nodes = new Map([...allTags].filter(([, tag]) => tag.kind === kind));
    const parents = new Map([...nodes.keys()].map((id) => [id, new Set()]));
    const children = new Map([...nodes.keys()].map((id) => [id, new Set()]));
    const connect = (parent, child) => {
      if (parent === child || !nodes.has(parent) || !nodes.has(child)) return;
      parents.get(child).add(parent); children.get(parent).add(child);
    };
    for (const tag of nodes.values()) {
      ids(tag.parent_ids).forEach((parent) => connect(parent, tag.id));
      ids(tag.child_ids).forEach((child) => connect(tag.id, child));
    }
    for (const row of catalogue.relations || []) {
      if (row.relation_type === "hierarchy") connect(String(row.parent_id), String(row.child_id));
    }
    const ordered = [...nodes.values()].sort(compare);
    const roots = ordered.filter((tag) => !parents.get(tag.id).size).map((tag) => tag.id);
    // Invalid cyclic input must remain inspectable instead of hanging the browser.
    if (!roots.length && ordered.length) roots.push(ordered[0].id);
    return { kind, allTags, nodes, parents, children, roots, ordered };
  }

  function walk(start, neighbors) {
    const seen = new Set(), pending = [...start];
    while (pending.length) {
      const id = pending.pop(); if (seen.has(id)) continue;
      seen.add(id); for (const next of neighbors.get(id) || []) pending.push(next);
    }
    return seen;
  }

  /** Search reveals ancestor routes. Local focus initially reveals all direct parents and children. */
  function visibleGraph(model, options = {}) {
    const expanded = new Set(options.expanded || []), query = normalized(options.query).trim();
    const matches = new Set(model.ordered.filter((tag) => !query || normalized([
      tag.name, ...(Array.isArray(tag.aliases) ? tag.aliases : [tag.aliases || ""]), tag.description || "", tag.conditions || ""
    ].join(" ")).includes(query)).map((tag) => tag.id));
    const visible = new Set(), starts = [];
    if (query) {
      for (const id of walk(matches, model.parents)) visible.add(id);
      for (const id of expanded) if (visible.has(id)) starts.push(id);
    } else if (options.local && model.nodes.has(String(options.focusId))) {
      const focusId = String(options.focusId); visible.add(focusId);
      for (const id of model.parents.get(focusId)) visible.add(id);
      if (options.expanded === undefined) expanded.add(focusId);
      starts.push(...visible);
    } else {
      const roots = options.rootId && model.nodes.has(String(options.rootId)) ? [String(options.rootId)] : model.roots;
      roots.forEach((id) => visible.add(id)); starts.push(...roots);
    }
    const walked = new Set();
    while (starts.length) {
      const id = starts.shift(); if (walked.has(id)) continue; walked.add(id);
      if (!expanded.has(id)) continue;
      for (const child of model.children.get(id) || []) { visible.add(child); starts.push(child); }
    }
    // 1.25.70：默认只保留「用得起的」节点 —— 自身 0 课 + 全树没子标签的先收起来，
    // 再把隐藏节点的祖辈全找回来（保住子路径），不让画面里只剩孤岛。
    const hideEmpty = options.hideEmpty !== false;
    const hasQuestions = (id) => Number(model.nodes.get(id)?.subtree_count || model.nodes.get(id)?.count || 0) > 0;
    const filtered = new Set(visible);
    if (hideEmpty) {
      const keep = new Set();
      for (const id of filtered) if (hasQuestions(id)) keep.add(id);
      let changed = true;
      while (changed) {
        changed = false;
        for (const id of filtered) {
          if (keep.has(id)) continue;
          for (const child of model.children.get(id) || []) {
            if (keep.has(child)) { keep.add(id); changed = true; break; }
          }
        }
      }
      for (const id of [...filtered]) if (!keep.has(id)) filtered.delete(id);
    }
    const nodes = new Map([...filtered].map((id) => [id, model.nodes.get(id)]).filter(([, tag]) => tag));
    const edges = [];
    for (const child of filtered) for (const parent of model.parents.get(child) || []) {
      if (filtered.has(parent)) edges.push({ parent, child, type: "hierarchy" });
    }
    if (options.related && model.kind === "knowledge") {
      for (const tag of [...nodes.values()]) for (const id of ids(tag.related_method_ids)) {
        const method = model.allTags.get(id);
        if (!method || method.kind !== "method") continue;
        nodes.set(id, { ...method, related: true }); edges.push({ parent: tag.id, child: id, type: "related_method" });
      }
    }
    return { nodes, edges, matches: query ? matches : new Set(), query, total: model.nodes.size, hidden: hideEmpty ? visible.size - filtered.size : 0 };
  }

  /** Layered DAG layout; shared children get one position and all their incoming connections. */
  function layoutGraph(graph) {
    const nodeIds = [...graph.nodes.keys()].sort((a, b) => compare(graph.nodes.get(a), graph.nodes.get(b)));
    const incoming = new Map(nodeIds.map((id) => [id, []])), outgoing = new Map(nodeIds.map((id) => [id, []]));
    for (const edge of graph.edges) {
      if (!incoming.has(edge.child) || !outgoing.has(edge.parent)) continue;
      incoming.get(edge.child).push(edge.parent); outgoing.get(edge.parent).push(edge.child);
    }
    const counts = new Map(nodeIds.map((id) => [id, incoming.get(id).length]));
    const depths = new Map(nodeIds.map((id) => [id, 0]));
    const pending = nodeIds.filter((id) => counts.get(id) === 0), handled = new Set();
    while (pending.length) {
      const id = pending.shift(); if (handled.has(id)) continue; handled.add(id);
      for (const child of outgoing.get(id)) {
        depths.set(child, Math.max(depths.get(child), depths.get(id) + 1));
        counts.set(child, counts.get(child) - 1); if (counts.get(child) === 0) pending.push(child);
      }
    }
    // Keep malformed relationships finite; the server rejects cycles on writes.
    nodeIds.filter((id) => !handled.has(id)).forEach((id) => depths.set(id, 0));
    const layers = [];
    nodeIds.forEach((id) => { const depth = depths.get(id); (layers[depth] ||= []).push(id); });
    const order = new Map();
    layers.forEach((layer) => {
      layer.sort((a, b) => {
        const average = (id) => {
          const values = incoming.get(id).filter((parent) => order.has(parent)).map((parent) => order.get(parent));
          return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : Number.MAX_SAFE_INTEGER;
        };
        return average(a) - average(b) || compare(graph.nodes.get(a), graph.nodes.get(b));
      });
      layer.forEach((id, index) => order.set(id, index));
    });
    // A flat directory puts every tag in one layer. A single column of hundreds of nodes
    // becomes an unreadable vertical ribbon, so wide layers wrap into a grid instead.
    const columnsFor = (layer) => (layer.length > 1 && layers.length === 1
      ? Math.ceil(Math.sqrt(layer.length * HEIGHT / WIDTH)) : 1);
    const gridColumns = new Map(layers.map((layer) => [layer, columnsFor(layer)]));
    const gridRows = (layer) => Math.ceil(layer.length / gridColumns.get(layer));
    const rows = Math.max(1, ...layers.map(gridRows));
    const contentHeight = rows * (HEIGHT + ROW_GAP) - ROW_GAP;
    const positions = new Map();
    layers.forEach((layer, depth) => {
      const columns = gridColumns.get(layer), columnWidth = WIDTH + COLUMN_GAP;
      layer.forEach((id, index) => {
        const row = Math.floor(index / columns), column = index % columns;
        const layerRows = gridRows(layer);
        positions.set(id, {
          x: MARGIN + (depth + column) * columnWidth,
          // Center each column vertically so a wrapped grid stays visually balanced.
          y: MARGIN + (contentHeight - (layerRows * (HEIGHT + ROW_GAP) - ROW_GAP)) / 2 + row * (HEIGHT + ROW_GAP),
          width: WIDTH, height: HEIGHT, depth
        });
      });
    });
    const widest = Math.max(...layers.map((layer) => gridColumns.get(layer)), 1);
    return { positions, width: MARGIN * 2 + (layers.length - 1 + widest - 1) * (WIDTH + COLUMN_GAP) + WIDTH,
      height: contentHeight + MARGIN * 2, layers };
  }

  function element(name, className, text) {
    const value = document.createElement(name); if (className) value.className = className;
    if (text !== undefined) value.textContent = text; return value;
  }
  function button(text, action, title) {
    const value = element("button", "tag-map-button", text); value.type = "button";
    value.dataset.mapAction = action; if (title) { value.title = title; value.setAttribute("aria-label", title); } return value;
  }

  function create(options) {
    if (!options?.host) throw new Error("标签地图缺少画布容器");
    const host = options.host, workspace = element("section", "tag-map-workspace");
    workspace.setAttribute("aria-label", "标签地图");
    const toolbar = element("div", "tag-map-toolbar"), context = element("div", "tag-map-context");
    const overview = button("全部结构", "overview", "回到当前类型的顶级标签"), focusButton = button("聚焦当前", "focus", "只看选中标签附近的上下级");
    const hideEmpty = button("只看用到的", "hide-empty", "把还没题目的标签收起来，画面就不乱了"); hideEmpty.setAttribute("aria-pressed", "true");
    const rootLabel = element("label", "tag-map-root-label"); rootLabel.append(element("span", "", "主题"));
    const rootSelect = element("select", "tag-map-root-select"); rootSelect.setAttribute("aria-label", "选择地图主题"); rootLabel.append(rootSelect);
    const related = button("相关方法", "related", "显示虚线连接的相关方法；相关关系不参与下级筛选"); related.setAttribute("aria-pressed", "false");
    const scope = element("span", "tag-map-scope"), resetLayout = button("整理位置", "reset-layout", "清除本次拖动，恢复自动排列；不会改变父子关系");
    toolbar.append(overview, focusButton, hideEmpty, rootLabel, related, resetLayout, scope);
    const viewport = element("div", "tag-map-viewport"); viewport.tabIndex = 0;
    viewport.setAttribute("aria-label", "标签关系画布，可用方向键平移、加减键缩放、数字 0 适合画布");
    const world = element("div", "tag-map-world tag-map-scene"), wires = document.createElementNS(NS, "svg");
    wires.classList.add("tag-map-wires"); wires.setAttribute("aria-hidden", "true");
    const nodeLayer = element("div", "tag-map-nodes"); world.append(wires, nodeLayer); viewport.append(world);
    const empty = element("div", "tag-map-empty"); empty.hidden = true; viewport.append(empty);
    const zoomTools = element("div", "tag-map-zoom-tools");
    const zoomOut = button("−", "zoom-out", "缩小标签地图"), zoomLabel = element("span", "tag-map-zoom-label", "100%");
    const zoomIn = button("+", "zoom-in", "放大标签地图"), fitButton = button("适合画布", "fit", "把当前可见结构放进画布");
    zoomTools.append(zoomOut, zoomLabel, zoomIn, fitButton); viewport.append(zoomTools);
    const footer = element("div", "tag-map-footer"), status = element("span", "tag-map-status");
    status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    const hint = element("span", "tag-map-hint", "拖动画布平移 · 拖动标签整理位置 · Ctrl + 滚轮缩放"); footer.append(status, hint);
    workspace.append(toolbar, context, viewport, footer); host.append(workspace);
    const state = { catalogue: { tags: [] }, kind: "knowledge", model: buildModel(), graph: null, layout: null,
      expanded: new Set(), selected: "", focusId: "", local: false, rootId: "", query: "", related: false,
      hideEmpty: true,
      busy: false, zoom: 1, tx: 24, ty: 24, touched: false, signature: "", initialized: false, pendingFit: false,
      positions: new Map(), offsets: new Map(), destroyed: false, suppressClick: false, gestures: new Map(), gesture: null };
    const listeners = [];
    const listen = (target, type, callback, settings) => { target.addEventListener(type, callback, settings); listeners.push(() => target.removeEventListener(type, callback, settings)); };
    const selectedTag = () => state.model.allTags.get(state.selected);
    const viewportSize = () => ({ width: viewport.clientWidth, height: viewport.clientHeight });
    const clearQuery = () => { if (state.query) { state.query = ""; options.onQueryChange?.(""); } };

    function applyTransform() {
      world.style.transform = `translate(${state.tx}px, ${state.ty}px) scale(${state.zoom})`;
      zoomLabel.textContent = Math.round(state.zoom * 100) + "%";
      viewport.dataset.zoom = String(state.zoom); viewport.dataset.panX = String(state.tx); viewport.dataset.panY = String(state.ty);
    }
    function zoomTo(value, x, y) {
      const next = clamp(value, .25, 2), size = viewportSize();
      x = x ?? size.width / 2; y = y ?? size.height / 2;
      state.tx = x - (x - state.tx) * next / state.zoom; state.ty = y - (y - state.ty) * next / state.zoom;
      state.zoom = next; state.touched = true; applyTransform();
    }
    function bounds() {
      const values = [...state.positions.values()]; if (!values.length) return { x: 0, y: 0, width: WIDTH, height: HEIGHT };
      const x = Math.min(...values.map((p) => p.x)), y = Math.min(...values.map((p) => p.y));
      return { x, y, width: Math.max(...values.map((p) => p.x + WIDTH)) - x, height: Math.max(...values.map((p) => p.y + HEIGHT)) - y };
    }
    // Fitting a very large directory must not shrink the text into an unreadable mosaic.
    // Below this scale the map stays pannable and the status line says so.
    const READABLE_ZOOM = .55;
    function fit(markTouched = true) {
      const size = viewportSize(); if (size.width < 20 || size.height < 20) { state.pendingFit = true; return; }
      const area = bounds(), padding = size.width < 480 ? 18 : 38;
      const raw = Math.min((size.width - padding * 2) / area.width, (size.height - padding * 2 - 36) / area.height, 1);
      state.zoom = clamp(raw, raw < READABLE_ZOOM ? READABLE_ZOOM : .25, 1);
      state.tx = (size.width - area.width * state.zoom) / 2 - area.x * state.zoom;
      state.ty = (size.height - area.height * state.zoom) / 2 - area.y * state.zoom;
      state.pendingFit = false; state.touched = markTouched; state.cropped = raw < READABLE_ZOOM;
      applyTransform();
    }
    function centerNode(id) {
      const position = state.positions.get(id); if (!position) return;
      const size = viewportSize(); if (!size.width || !size.height) { state.pendingFit = true; return; }
      state.zoom = Math.max(state.zoom, Math.min(1, (size.width - 38) / WIDTH));
      state.tx = size.width / 2 - (position.x + WIDTH / 2) * state.zoom;
      state.ty = size.height / 2 - (position.y + HEIGHT / 2) * state.zoom; applyTransform();
    }
    function renderContext() {
      context.replaceChildren(); const tag = selectedTag(); context.hidden = !tag;
      focusButton.disabled = !tag || state.busy; overview.disabled = state.busy;
      rootSelect.disabled = state.busy; related.disabled = state.busy;
      related.hidden = state.kind !== "knowledge"; related.setAttribute("aria-pressed", String(state.related));
      if (!tag) return;
      const name = element("strong", "tag-map-context-name", tag.name); name.title = tag.name;
      const label = element("span", "tag-map-context-caption", "选中标签"); context.append(label, name);
      const actions = [ ["查看题目", "find-questions", options.onFindQuestions], ["修改标签", "edit", options.onEdit] ];
      if (["knowledge", "method"].includes(tag.kind)) actions.push(["添加子标签", "add-child", options.onAddChild], ["修改父级", "edit-parents", options.onEditParents]);
      if (tag.kind === "knowledge") actions.push(["相关方法", "related-methods", options.onRelatedMethods]);
      for (const [text, action, callback] of actions) if (callback) {
        const control = button(text, action); control.disabled = state.busy || (action === "add-child" && tag.active === false); context.append(control);
      }
    }
    function setSelected(id, notify = true) {
      id = String(id || ""); if (id && !state.model.allTags.has(id)) return;
      state.selected = id; renderContext();
      nodeLayer.querySelectorAll(".tag-map-node").forEach((node) => {
        const selected = node.dataset.tagId === id; node.classList.toggle("selected", selected);
        node.querySelector(".tag-map-node-select").setAttribute("aria-pressed", String(selected));
      });
      if (notify && id) options.onSelect?.(selectedTag(), { source: "map" });
    }
    function renderWires() {
      wires.replaceChildren();
      const area = bounds(); wires.setAttribute("width", String(Math.max(state.layout?.width || 0, area.x + area.width + 50)));
      wires.setAttribute("height", String(Math.max(state.layout?.height || 0, area.y + area.height + 50)));
      for (const edge of state.graph?.edges || []) {
        const a = state.positions.get(edge.parent), b = state.positions.get(edge.child); if (!a || !b) continue;
        const path = document.createElementNS(NS, "path"); const ax = a.x + WIDTH, ay = a.y + HEIGHT / 2, bx = b.x, by = b.y + HEIGHT / 2;
        const bend = Math.max(42, Math.abs(bx - ax) * .5);
        path.setAttribute("d", `M ${ax} ${ay} C ${ax + bend} ${ay}, ${bx - bend} ${by}, ${bx} ${by}`);
        path.dataset.parentId = edge.parent; path.dataset.childId = edge.child; path.dataset.relationType = edge.type;
        path.classList.toggle("related", edge.type === "related_method");
        path.classList.toggle("selected", edge.parent === state.selected || edge.child === state.selected);
        wires.append(path);
      }
    }
    function renderGraph({ fitView = false, preserveFocus = "", centerOnChildren = "" } = {}) {
      const oldAnchor = state.positions.get(state.selected);
      const graph = visibleGraph(state.model, { expanded: state.expanded, query: state.query, focusId: state.focusId,
        local: state.local, rootId: state.rootId, related: state.related, hideEmpty: state.hideEmpty });
      state.graph = graph; state.layout = layoutGraph(graph); state.positions.clear();
      for (const [id, position] of state.layout.positions) {
        const offset = state.offsets.get(id) || { x: 0, y: 0 };
        state.positions.set(id, { ...position, x: position.x + offset.x, y: position.y + offset.y });
      }
      nodeLayer.replaceChildren();
      for (const [id, tag] of graph.nodes) {
        const position = state.positions.get(id), node = element("article", "tag-map-node"); node.dataset.tagId = id;
        node.style.left = position.x + "px"; node.style.top = position.y + "px";
        node.classList.toggle("selected", id === state.selected); node.classList.toggle("match", graph.matches.has(id));
        node.classList.toggle("inactive", tag.active === false); node.classList.toggle("related-node", !!tag.related);
        const select = button("", "select"); select.className = "tag-map-node-select"; select.dataset.tagId = id;
        select.setAttribute("aria-pressed", String(id === state.selected)); select.title = tag.name + (tag.description ? "\n" + tag.description : "");
        const name = element("span", "tag-map-node-name", tag.name), meta = element("span", "tag-map-node-meta");
        const parentCount = state.model.parents.get(id)?.size || 0;
        meta.textContent = (tag.related ? "相关方法" : KIND_NAMES[tag.kind] || "标签") + (parentCount > 1 ? " · " + parentCount + " 个父级" : "") + (tag.active === false ? " · 停用" : "");
        select.append(name, meta); select.disabled = state.busy;
        const footer = element("div", "tag-map-node-footer");
        const direct = tag.direct_count ?? tag.count, subtree = tag.subtree_count ?? direct;
        const count = button(direct === undefined ? "查看题目" : "本级 " + direct + " · 含下级 " + subtree, "find-questions");
        count.className = "tag-map-node-count"; count.dataset.tagId = id; count.title = "查看此标签及其下级对应的题目（按题去重）";
        count.disabled = state.busy || !options.onFindQuestions; footer.append(count);
        const childCount = state.model.children.get(id)?.size || 0;
        if (childCount) {
          const expand = button(state.expanded.has(id) ? "−" : "+" + childCount, "expand", (state.expanded.has(id) ? "收起" : "展开") + tag.name + "的直接子标签");
          expand.className = "tag-map-expand"; expand.dataset.tagId = id; expand.setAttribute("aria-expanded", String(state.expanded.has(id))); expand.disabled = state.busy; footer.append(expand);
        }
        node.append(select, footer); nodeLayer.append(node);
      }
      world.style.width = state.layout.width + "px"; world.style.height = state.layout.height + "px";
      renderWires(); renderContext();
      empty.hidden = graph.nodes.size > 0;
      if (!empty.hidden) {
        const mark = element("span", "tag-map-empty-mark", state.query ? "🔍" : "○");
        if (state.query) mark.classList.add("is-emoji");
        empty.replaceChildren(mark,
          element("strong", "", state.query ? "没有找到匹配的标签" : "这里还没有" + (KIND_NAMES[state.kind] || "标签")),
          element("p", "", state.query ? "试试标签名称、别名或说明。搜索只定位目录，不会修改标签。" : "可以新建自己的标签，也可以预览并追加系统模板。地图不会替你创建目录。"));
      }
      const baseCount = [...graph.nodes.values()].filter((tag) => !tag.related).length;
      scope.textContent = state.local ? "当前标签附近" : state.rootId ? "当前主题" : "顶级与已展开分支";
      status.textContent = state.query ? "找到 " + graph.matches.size + " 个标签 · 已显示上级路径" : "显示 " + baseCount + " / " + graph.total + " 个标签";
      if (graph.hidden) status.textContent += ` · 收起 ${graph.hidden} 个还没题目的标签`;
      if (state.related) status.textContent += " · 虚线仅表示相关方法";
      if (state.cropped && graph.nodes.size) status.textContent += " · 已保持文字可读，可拖动画布查看其余标签";
      if (preserveFocus) nodeLayer.querySelector('.tag-map-expand[data-tag-id="' + CSS.escape(preserveFocus) + '"]')?.focus({ preventScroll: true });
      if (fitView) fit(false);
      else {
        const nextAnchor = state.positions.get(state.selected);
        if (oldAnchor && nextAnchor) { state.tx += (oldAnchor.x - nextAnchor.x) * state.zoom; state.ty += (oldAnchor.y - nextAnchor.y) * state.zoom; }
        // 1.25.48：展开某一支后，把这一支的新节点移到视野中间；不缩放，尺寸不变。
        const kids = centerOnChildren ? [...(state.model.children.get(centerOnChildren) || [])].map((child) => state.positions.get(child)).filter(Boolean) : [];
        if (kids.length) {
          const xs = kids.map((position) => position.x), ys = kids.map((position) => position.y);
          const size = viewportSize();
          const cx = (Math.min(...xs) + Math.max(...xs) + WIDTH) / 2, cy = (Math.min(...ys) + Math.max(...ys) + HEIGHT) / 2;
          state.tx = size.width / 2 - cx * state.zoom; state.ty = size.height / 2 - cy * state.zoom;
          state.touched = true;
        }
        applyTransform();
      }
    }
    function renderRoots() {
      const previous = state.rootId; rootSelect.replaceChildren(); rootSelect.add(new Option("全部主题", ""));
      for (const id of state.model.roots) rootSelect.add(new Option(state.model.nodes.get(id).name, id));
      if (!state.model.nodes.has(previous)) state.rootId = ""; rootSelect.value = state.rootId;
    }
    function focus(id, settings = {}) {
      id = String(id); const tag = state.model.allTags.get(id); if (!tag) return false;
      if (tag.kind !== state.kind) { state.kind = tag.kind; state.model = buildModel(state.catalogue, state.kind); state.expanded = new Set(state.model.roots); state.offsets.clear(); renderRoots(); }
      state.selected = id; state.focusId = id; state.local = settings.local !== false; state.rootId = ""; rootSelect.value = ""; clearQuery();
      if (state.local) state.expanded = new Set([id]);
      if (!state.local) for (const ancestor of walk([id], state.model.parents)) state.expanded.add(ancestor);
      renderGraph({ fitView: true }); if (state.local) centerNode(id); return true;
    }
    function update(values = {}) {
      if (state.destroyed) return;
      const kind = values.kind || state.kind, kindChanged = state.initialized && kind !== state.kind;
      const catalogueChanged = values.catalogue && values.catalogue !== state.catalogue;
      const oldSelected = state.selected, oldQuery = normalized(state.query).trim(), previouslyEmpty = !state.model.nodes.size;
      if (values.catalogue) state.catalogue = values.catalogue;
      state.kind = kind; state.model = buildModel(state.catalogue, kind);
      if (!state.initialized || kindChanged) {
        state.expanded = new Set(state.model.roots); state.local = false; state.focusId = ""; state.rootId = ""; state.offsets.clear(); state.touched = false;
      }
      if (values.query !== undefined) {
        state.query = String(values.query || ""); const nextQuery = normalized(state.query).trim();
        if (nextQuery && !oldQuery) { state.browseExpanded = new Set(state.expanded); state.expanded = new Set(); }
        else if (!nextQuery && oldQuery) state.expanded = state.browseExpanded || new Set(state.model.roots);
      }
      if (values.busy !== undefined) state.busy = !!values.busy;
      if (values.focusId !== undefined) {
        const id = String(values.focusId || ""); if (state.model.allTags.has(id)) state.selected = id; else if (!id) state.selected = "";
      }
      if (state.selected && !state.model.allTags.has(state.selected)) state.selected = "";
      if (state.focusId && !state.model.nodes.has(state.focusId)) { state.focusId = ""; state.local = false; }
      for (const id of state.expanded) if (!state.model.nodes.has(id)) state.expanded.delete(id);
      const signature = JSON.stringify([state.kind, state.query, state.busy, [...state.expanded], state.local, state.focusId, state.rootId, state.related,
        [...state.model.allTags.values()].map((tag) => [tag.id, tag.kind, tag.name, tag.description, tag.aliases, tag.conditions, tag.active, tag.parent_ids, tag.child_ids, tag.related_method_ids, tag.direct_count, tag.subtree_count, tag.count])]);
      if (signature !== state.signature) {
        const fitView = !state.initialized || kindChanged || (previouslyEmpty && state.model.nodes.size > 0) || normalized(state.query).trim() !== (state.graph?.query || "");
        state.signature = signature; renderRoots(); renderGraph({ fitView });
      } else if (oldSelected !== state.selected || catalogueChanged) { setSelected(state.selected, false); renderWires(); }
      state.initialized = true;
    }

    listen(workspace, "click", (event) => {
      if (state.suppressClick) { state.suppressClick = false; event.preventDefault(); event.stopPropagation(); return; }
      const control = event.target.closest("[data-map-action]"); if (!control || control.disabled || state.busy) return;
      const action = control.dataset.mapAction, id = control.dataset.tagId, tag = id ? state.model.allTags.get(id) : selectedTag();
      if (action === "select") { setSelected(id); renderWires(); }
      else if (action === "expand") {
        // 1.25.48：展开以后新节点可能落在画布外，人只看到线不见了。展开后把这一支
        // 的新节点移到视野中间（不缩放，保持当前大小）；收起源接在原位。
        const opening = !state.expanded.has(id);
        opening ? state.expanded.add(id) : state.expanded.delete(id);
        renderGraph({ preserveFocus: id, ...(opening ? { centerOnChildren: id } : {}) }); state.signature = "";
      } else if (action === "overview") {
        state.local = false; state.focusId = ""; state.rootId = ""; rootSelect.value = ""; clearQuery();
        state.expanded = new Set(state.model.roots); state.offsets.clear(); renderGraph({ fitView: true }); state.signature = "";
      } else if (action === "focus" && tag) focus(tag.id, { local: true });
      else if (action === "related") { state.related = !state.related; renderGraph({ fitView: true }); state.signature = ""; }
      else if (action === "hide-empty") { state.hideEmpty = !state.hideEmpty; hideEmpty.setAttribute("aria-pressed", String(state.hideEmpty)); renderGraph({ fitView: true }); state.signature = ""; }
      else if (action === "zoom-in") zoomTo(state.zoom * 1.2);
      else if (action === "zoom-out") zoomTo(state.zoom / 1.2);
      else if (action === "fit") fit();
      else if (action === "reset-layout") { state.offsets.clear(); renderGraph({ fitView: true }); }
      else if (tag) ({ "find-questions": options.onFindQuestions, edit: options.onEdit, "add-child": options.onAddChild,
        "edit-parents": options.onEditParents, "related-methods": options.onRelatedMethods })[action]?.(tag);
    }, true);
    listen(rootSelect, "change", () => {
      state.rootId = rootSelect.value; state.local = false; state.focusId = ""; clearQuery();
      state.expanded = new Set(state.rootId ? [state.rootId] : state.model.roots); state.offsets.clear();
      renderGraph({ fitView: true }); state.signature = "";
    });
    listen(viewport, "wheel", (event) => {
      if (event.target.closest("select,input") || state.busy) return;
      if (event.ctrlKey || event.metaKey) {
        event.preventDefault(); const rect = viewport.getBoundingClientRect();
        zoomTo(state.zoom * Math.exp(-event.deltaY * .002), event.clientX - rect.left, event.clientY - rect.top);
      } else {
        // Native page scrolling is retained; a deliberate Shift + wheel pans the map horizontally.
        if (!event.shiftKey) return; event.preventDefault(); state.tx -= event.deltaY + event.deltaX; state.touched = true; applyTransform();
      }
    }, { passive: false });
    const point = (event) => ({ x: event.clientX, y: event.clientY });
    const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    const midpoint = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
    listen(viewport, "pointerdown", (event) => {
      if (state.busy || (event.button !== 0 && event.button !== 1) || event.target.closest(".tag-map-zoom-tools,input,select,.tag-map-expand,.tag-map-node-count")) return;
      const node = event.target.closest(".tag-map-node"), p = point(event);
      state.gestures.set(event.pointerId, p);
      if (state.gestures.size === 2) {
        const pair = [...state.gestures.values()], center = midpoint(...pair), rect = viewport.getBoundingClientRect();
        state.gesture = { type: "pinch", startDistance: distance(...pair), zoom: state.zoom,
          worldX: (center.x - rect.left - state.tx) / state.zoom, worldY: (center.y - rect.top - state.ty) / state.zoom, moved: true };
        state.suppressClick = true;
      } else state.gesture = { type: node ? "node" : "pan", pointerId: event.pointerId, id: node?.dataset.tagId,
        x: p.x, y: p.y, tx: state.tx, ty: state.ty, moved: false,
        offset: { ...(state.offsets.get(node?.dataset.tagId) || { x: 0, y: 0 }) } };
      if (!node) { viewport.focus({ preventScroll: true }); event.preventDefault(); }
      if (!node || state.gesture?.type === "pinch") viewport.setPointerCapture?.(event.pointerId);
    });
    listen(viewport, "pointermove", (event) => {
      if (!state.gestures.has(event.pointerId) || !state.gesture) return;
      state.gestures.set(event.pointerId, point(event)); const gesture = state.gesture;
      if (gesture.type === "pinch" && state.gestures.size >= 2) {
        const pair = [...state.gestures.values()].slice(0, 2), center = midpoint(...pair), rect = viewport.getBoundingClientRect();
        state.zoom = clamp(gesture.zoom * distance(...pair) / Math.max(gesture.startDistance, 1), .25, 2);
        state.tx = center.x - rect.left - gesture.worldX * state.zoom; state.ty = center.y - rect.top - gesture.worldY * state.zoom;
        state.touched = true; applyTransform(); return;
      }
      if (event.pointerId !== gesture.pointerId) return;
      const dx = event.clientX - gesture.x, dy = event.clientY - gesture.y;
      if (!gesture.moved && Math.hypot(dx, dy) < 4) return;
      if (!gesture.moved && gesture.type === "node") viewport.setPointerCapture?.(event.pointerId);
      gesture.moved = true; state.suppressClick = true; state.touched = true; viewport.classList.add("dragging");
      if (gesture.type === "pan") { state.tx = gesture.tx + dx; state.ty = gesture.ty + dy; applyTransform(); }
      else if (gesture.type === "node") {
        const offset = { x: gesture.offset.x + dx / state.zoom, y: gesture.offset.y + dy / state.zoom };
        state.offsets.set(gesture.id, offset); const base = state.layout.positions.get(gesture.id);
        if (!base) return; const position = { ...base, x: base.x + offset.x, y: base.y + offset.y }; state.positions.set(gesture.id, position);
        const node = nodeLayer.querySelector('[data-tag-id="' + CSS.escape(gesture.id) + '"]');
        if (node) { node.style.left = position.x + "px"; node.style.top = position.y + "px"; } renderWires();
      }
    });
    function endGesture(event, cancel = false) {
      if (!state.gestures.has(event.pointerId)) return;
      const gesture = state.gesture;
      if (cancel && gesture && gesture.type !== "pinch") {
        if (gesture.type === "node") { state.offsets.set(gesture.id, gesture.offset); renderGraph(); }
        else { state.tx = gesture.tx; state.ty = gesture.ty; applyTransform(); }
      }
      state.gestures.delete(event.pointerId); viewport.releasePointerCapture?.(event.pointerId);
      if (!state.gestures.size || gesture?.type === "pinch") { state.gestures.clear(); state.gesture = null; viewport.classList.remove("dragging"); }
      // Pointer-generated click follows pointerup synchronously; later unrelated clicks must not be swallowed.
      if (state.suppressClick) root.setTimeout(() => { state.suppressClick = false; }, 0);
    }
    listen(viewport, "pointerup", (event) => endGesture(event));
    listen(viewport, "pointercancel", (event) => endGesture(event, true));
    listen(viewport, "keydown", (event) => {
      if (event.isComposing || event.repeat || state.busy || event.target !== viewport || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "+" || event.key === "=") zoomTo(state.zoom * 1.2);
      else if (event.key === "-") zoomTo(state.zoom / 1.2);
      else if (event.key === "0" || event.key === "Home") fit();
      else if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
        state.tx += event.key === "ArrowLeft" ? 64 : event.key === "ArrowRight" ? -64 : 0;
        state.ty += event.key === "ArrowUp" ? 64 : event.key === "ArrowDown" ? -64 : 0; state.touched = true; applyTransform();
      } else return; event.preventDefault(); event.stopPropagation();
    });
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => {
      if (state.destroyed) return; if (state.pendingFit || !state.touched) fit(false);
    }) : null;
    observer?.observe(viewport);
    update();
    return { update, focus, fit, element: workspace,
      getState: () => ({ kind: state.kind, selected: state.selected, focusId: state.focusId, local: state.local, rootId: state.rootId,
        query: state.query, zoom: state.zoom, tx: state.tx, ty: state.ty, visibleIds: [...(state.graph?.nodes.keys() || [])], expanded: [...state.expanded] }),
      destroy() { state.destroyed = true; observer?.disconnect(); listeners.forEach((remove) => remove()); workspace.remove(); } };
  }
  root.QBLibraryTagMap = { create, buildModel, visibleGraph, layoutGraph };
})(typeof window !== "undefined" ? window : globalThis);
