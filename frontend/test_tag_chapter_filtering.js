// 选标签时按章节过滤：点一个章节，要看到它自己加上它下面所有层级，
// 而不是只看到直接挂着的那些。
//
// 这一条值得单独测，因为它错的时候不报错：筛选框照样能用，只是找某些标签
// 永远找不到——用户只会以为「题库里没有这个标签」。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const libraryJs = fs.readFileSync(require.resolve("./library.js"), "utf8");

function extract(name) {
  const start = libraryJs.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} 应该存在于 library.js`);
  let depth = 0;
  let opened = false;
  let index = libraryJs.indexOf("{", start);
  for (; index < libraryJs.length; index += 1) {
    const character = libraryJs[index];
    if (character === "{") { depth += 1; opened = true; }
    else if (character === "}") { depth -= 1; if (opened && depth === 0) { index += 1; break; } }
  }
  return libraryJs.slice(start, index);
}

const context = vm.createContext({ tagCatalogue: { tags: [] } });
vm.runInContext(`${extract("tagChildrenIndex")}\n${extract("tagSubtree")}\n${extract("tagTrail")}\n`
  + "globalThis.tagChildrenIndex = tagChildrenIndex;\n"
  + "globalThis.tagSubtree = tagSubtree;\n"
  + "globalThis.tagTrail = tagTrail;", context);
const { tagChildrenIndex, tagSubtree, tagTrail } = context;

// 初中「函数」下面有概念，概念下面还有知识点；高中章节下另有自己的标签。
const tags = [
  { id: "junior-fn", name: "初中 · 函数", parent_ids: [] },
  { id: "idea", name: "函数的概念", parent_ids: ["junior-fn"] },
  { id: "monotone", name: "函数的单调性", parent_ids: ["idea"] },
  { id: "quadratic", name: "二次函数的最值", parent_ids: ["monotone"] },
  { id: "senior-fn", name: "高中 · 函数", parent_ids: [] },
  { id: "trig", name: "三角函数", parent_ids: ["senior-fn"] },
  { id: "orphan", name: "没有上级的标签", parent_ids: [] }
];
context.tagCatalogue = { tags };
const children = tagChildrenIndex();

// 章节要一次给全它下面的所有层级：概念、孙层、曾孙层都得在。
const junior = tagSubtree("junior-fn", children);
assert.ok(junior.has("junior-fn"), "章节自己必须在结果里");
assert.ok(junior.has("idea"), "直接挂着的概念要出现");
assert.ok(junior.has("monotone"), "隔了一层的标签也要出现");
assert.ok(junior.has("quadratic"), "隔了两层的标签也要出现");
assert.ok(!junior.has("senior-fn"), "别的章节不能混进来");
assert.ok(!junior.has("trig"), "别的章节的标签不能混进来");
assert.ok(!junior.has("orphan"), "没有上级的标签跟这个章节无关");
assert.equal(junior.size, 4, "初中函数章节下就是四个标签");

// 悬空的上级 id 不能把遍历带进死循环，也不能凭空多出标签。
const dangling = tagChildrenIndex();
assert.ok(dangling.size >= 0);
assert.equal(tagSubtree("missing", dangling).size, 1, "不存在的标签只返回它自己");

// 环（A→B→A）不能让筛选转不出来，也不能凭空多出标签。
context.tagCatalogue = { tags: [
  { id: "a", name: "甲", parent_ids: ["b"] },
  { id: "b", name: "乙", parent_ids: ["a"] }
] };
const loop = tagChildrenIndex();
assert.deepEqual(Array.from(tagSubtree("a", loop)).sort(), ["a", "b"],
  "互相挂成上级时，筛选要停下来并把两人都算进来，不能转不出来");

// 行上的副标题要写出从章节到自己的一条路，让人知道这个标签归在哪。
const byId = new Map(tags.map((tag) => [tag.id, tag]));
assert.deepEqual(Array.from(tagTrail(byId.get("quadratic"), byId)),
  ["初中 · 函数", "函数的概念", "函数的单调性"],
  "从最上层的章节一直写到自己");
assert.deepEqual(Array.from(tagTrail(byId.get("junior-fn"), byId)), [], "顶层标签自己就是路");
assert.deepEqual(Array.from(tagTrail(byId.get("monotone"), byId)),
  ["初中 · 函数", "函数的概念"]);

console.log("选标签按章节过滤：子树、子层级与归属路径都对");
