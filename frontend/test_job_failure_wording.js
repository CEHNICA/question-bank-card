// 任务失败提示的措辞：配好之后，那句「尚未配置并通过测试」就不能再出现。
//
// 这一条值得单独测，因为它错的时候不报错、也不崩溃，只是让人以为设置没弄好，
// 于是反复回去检查已经配好的东西——比没有提示更难查。
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

const context = vm.createContext({ state: { ai: { mode: "api" } } });
vm.runInContext(`${extract("describeJobFailure")}\n${extract("aiReady")}\n`
  + "globalThis.describeJobFailure = describeJobFailure;\n"
  + "globalThis.aiReady = aiReady;", context);
const { describeJobFailure, aiReady } = context;

// 界面上「能不能用」和按钮显不显示必须走同一个口径。
const jobButtonsSource = libraryJs.slice(
  libraryJs.indexOf("function jobButtons(item)"),
  libraryJs.indexOf("function render()"));
assert.doesNotMatch(jobButtonsSource, /state\.ai\.api_ready/,
  "jobButtons 不再自己判断能不能生成，改用 aiReady，两处判定不能各写一份");

// ---- 服务后来配好了：那次的失败原因已经过期，不能原样摆出来 ----
const ready = describeJobFailure("tags",
  "标签与答案 API 尚未配置并通过显式测试，已暂停 API 生成；请打开“标签与答案”设置。", true);
assert.match(ready.head, /上次没跑成/, "讲的是过去发生的事，不是此刻的状态");
assert.doesNotMatch(ready.head + ready.body, /尚未配置/,
  "服务已经配好时，不能再说「尚未配置」——这是让人反复回去检查设置的根源");
assert.doesNotMatch(ready.head, /未完成/, "用现在时讲一件过去的事，读起来像正在出故障");
assert.match(ready.body, /补齐缺的知识点标签/, "要指出整批落下的活在哪儿补");
assert.match(ready.title, /已经配好/, "悬停说明也��交代清楚此刻已经可��重来");

const readyAnswer = describeJobFailure("answer",
  "标签与答案 API 尚未配置并通过显式测试，已暂停 API 生成。", true);
assert.match(readyAnswer.body, /补齐缺的参考答案/, "答案这项指向自己那个批量入口");
assert.doesNotMatch(readyAnswer.head + readyAnswer.body, /知识点标签/,
  "答案的提示不该把标签的入口念出来");

// ---- 服务确实还没配好：该说还是要说，而且要说清楚去哪儿配 ----
const notReady = describeJobFailure("tags",
  "标签与答案 API 尚未配置并通过显式测试，已暂停 API 生成。", false);
assert.match(notReady.head, /未完成/, "还没配好时，老实说未完成");
assert.match(notReady.body, /尚未配置/, "还没配好时，失败原因要原样说出来");
assert.match(notReady.body2, /设置 → API 配置/, "要给出能照着做的去处");

// ---- 与配置无关的失败（内容违规、超时等）：照实说，不改口径 ----
for (const readyNow of [true, false]) {
  const other = describeJobFailure("answer", "模型返回的内容不是有效 JSON", readyNow);
  assert.match(other.head, /未完成/);
  assert.match(other.body, /模型返回的内容不是有效 JSON/, "别的原因要原样透出");
  assert.equal(other.body2, "", "跟配置无关时，不要硬塞一句「请检查设置」");
  assert.equal(other.title, "模型返回的内容不是有效 JSON");
}

// ---- 能不能用：一个口径，模式不同结果不同 ----
const api = { mode: "api", api_ready: true, ready: false };
const unready = { mode: "api", api_ready: false, ready: false };
const assistant = { mode: "assistant", api_ready: false, ready: true };
for (const [name, snapshot, tags, answer] of [
  ["API 模式且已就绪", api, true, true],
  ["API 模式但没测通", unready, false, false],
  ["助手模式只管标签", assistant, true, false]
]) {
  context.state.ai = snapshot;
  assert.equal(aiReady("tags"), tags, `${name}：标签的判定`);
  assert.equal(aiReady("answer"), answer, `${name}：答案的判定`);
}

console.log("任务失败提示措辞检查通过：配置已就绪时不再复述过期原因，未就绪时仍指明去处，其他原因原样透出。");
