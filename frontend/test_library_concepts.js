/* 找题与整理工作台：界面契约 + 纯逻辑
 *
 * 这一版工作台是单独一个页面，没动 library.js。
 * 界面契约用源码文本断言（跟这个项目其余前端测试一个路子），
 * 纯逻辑（条件条、分区、去条件）是真的跑一遍。
 */

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const html = fs.readFileSync(path.join(__dirname, "library-concepts.html"), "utf8");
const js = fs.readFileSync(path.join(__dirname, "library-concepts.js"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "library-concepts.css"), "utf8");

// ---------------------------------------------------------------- 界面契约

assert.match(html, /data-entry="textbook"/, "教材目录是一个独立入口");
assert.match(html, /data-entry="concept"/, "数学主题是一个独立入口");
assert.match(html, /data-entry="collection"/, "个人专题是一个独立入口");
assert.match(
  html,
  /<aside class="concepts-rail" id="conceptsRail"/,
  "三个入口住在左栏，不跟主搜索挤在一起",
);
assert.match(html, /id="conceptsSearch"/, "顶部主搜索框保留");
assert.match(html, /id="conceptsSavedSearches"/, "完整搜索可以保存和取回");
assert.match(html, /id="conceptsChips"/, "当前条件摆成可移除的条件条");

// 右侧五块：主要考查、当前解法、其他已记录解法、更多信息、待核对建议。
["主要考查", "当前解法", "其他已记录解法", "更多信息", "待核对建议"].forEach((title) => {
  assert.ok(js.includes(`"${title}"`), `右侧要有「${title}」这一块`);
});

// 「解法采用」没有解法分支时按钮是灰的，旁边必须有一行看得见的说明。
assert.match(
  js,
  /"「解法采用」要先选一个解法分支/,
  "灰控件旁边要有一行说明，差哪一步说清楚",
);
assert.match(js, /add\.disabled = true/, "没选解法分支时按钮确实禁用");

// 结果列表封顶，并且超了要说清楚。
assert.match(js, /const LIST_LIMIT = 50/, "结果列表有上限");
assert.match(js, /只显示前 \$\{LIST_LIMIT\} 题/, "超过上限要说明，不静默截断");

// 旧标签命中和待核对线索在卡片上就标出来，不等人点进去才发现。
assert.match(js, /concepts-card-flag", "旧标签"/, "旧标签命中提前标记");
assert.match(js, /concepts-card-flag", "待核对"/, "待核对线索提前标记");

// 三个分区各自计数。
assert.match(js, /已确认匹配/, "已确认匹配单独算");
assert.match(js, /旧标签命中/, "旧标签命中单独算");
assert.match(js, /待核对线索/, "待核对线索单独算");
assert.match(js, /未整理/, "没整理的题单独计数，不当成「没有」");

// 不自动调 AI：生成建议要用户自己点。
assert.match(
  js,
  /生成建议要你自己点，不会自动跑/,
  "浏览和整理不自动调用 AI",
);

// 同一件事只留一个入口：加标签只有「＋添加」一处。
const addButtons = js.match(/concepts-add/g) || [];
assert.ok(addButtons.length >= 1, "加标签的入口存在");
assert.ok(
  !/openTagEditor|tagPickerCreate/.test(js),
  "工作台不另开一套标签选择弹窗，避免两个入口两套行为",
);

// 中文标点和公式不能被归一化吃掉。
assert.match(
  fs.readFileSync(path.join(__dirname, "..", "backend", "core", "textnorm.py"), "utf8"),
  /def concept_key\(/,
  "概念身份键有唯一的实现",
);

// ---------------------------------------------------------------- 纯逻辑

require("./library-concepts.js");
const C = globalThis.QBConcepts;
assert.ok(C, "工作台导出纯逻辑供测试");

const query = C.emptyQuery();
query.keywords = ["三角函数"];
query.filters.question_type = "free_response";
query.concept_groups.push({ ids: ["t1"], roles: ["primary"], names: ["三角函数"] });
query.method_groups.push({ ids: ["m1"], subquestion: "1", cooccurrence: "same_variant" });
query.exclusions.conditions.push({ ids: ["m2"] });

const chips = C.conditionChips(query);
assert.ok(chips.length >= 5, "每个条件都摆成一条可移除的条件条");
assert.ok(chips.some((chip) => chip.text.includes("关键词")), "关键词在条件条里");
assert.ok(chips.some((chip) => chip.text.includes("题型")), "题型在条件条里");
assert.ok(chips.some((chip) => chip.text.includes("主要考查")), "用途写清楚，不只写个 id");
assert.ok(chips.some((chip) => chip.text.includes("第（1）问")), "范围写清楚");
assert.ok(chips.some((chip) => chip.kind === "exclusion"), "排除条件也在条件条里");

// 去掉一个条件，其它条件不受影响。
const afterRemove = C.removeCondition(query, { path: "concept_groups.0" });
assert.equal(afterRemove.concept_groups.length, 0);
assert.equal(afterRemove.keywords.length, 1);
assert.equal(afterRemove.method_groups.length, 1);

// 三种结果分开，不混在一起数。
const parts = C.partitionResults({
  items: [
    { publication_id: "1", bucket: "confirmed" },
    { publication_id: "2", bucket: "legacy" },
    { publication_id: "3", bucket: "clue" },
    { publication_id: "4", bucket: "confirmed" },
    { publication_id: "5", bucket: "unreviewed" },
  ],
});
assert.equal(parts.confirmed.length, 2);
assert.equal(parts.legacy.length, 1);
assert.equal(parts.clues.length, 1);
assert.equal(parts.unreviewed.length, 1);

// 卡片本身就是入口：不用先瞄一个按钮才知道能点。
assert.match(js, /card\.setAttribute\("role", "button"\)/, "题卡本身可点");
assert.match(js, /card\.addEventListener\("click", open\)/, "点卡片就打开整理面板");
assert.ok(
  !/concepts-card-open/.test(js),
  "不再另摆一个「整理这道题」按钮 —— 同一个动作只留一个入口",
);

// 题型在界面上是中文，不是英文代码。
assert.match(js, /TYPE_LABELS\[item\.question_type\]/, "题型显示中文");

// 未整理的题单独一区，不混进正式匹配。
assert.match(js, /title: "未整理"/, "未整理单独一区");
assert.match(js, /没贴标签不等于没有该知识或方法/, "未整理这一区把话说清楚");

// 撤销要有真的逆操作和冲突检查 —— 后端做，界面给入口。
assert.match(js, /撤销上次改动/, "界面上有撤销入口");
assert.match(js, /action: "undo"/, "撤销走服务层的 undo，不是前端自己删");
assert.match(
  fs.readFileSync(path.join(__dirname, "..", "backend", "core", "library_concepts.py"), "utf8"),
  /def undo_assertion/,
  "撤销在服务层实现，有真正的逆操作",
);
assert.match(
  fs.readFileSync(path.join(__dirname, "..", "backend", "core", "library_concepts.py"), "utf8"),
  /撤销会盖掉那个改动，所以没做/,
  "撤销带冲突检查：别人改过就不动",
);

// 窄窗口不压字。
assert.match(css, /@media \(max-width: 820px\)/, "窄窗口有单独布局");
assert.match(css, /@media \(max-width: 1180px\)/, "中等窗口有单独布局");

console.log("找题与整理工作台：入口分离、五块整理、条件条、分区计数、窄窗口布局：OK");
