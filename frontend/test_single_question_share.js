"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "eagle/index.html"), "utf8");

function load() {
  const requests = [];
  const notices = [];
  const values = {
    commPubTitle: { value: "（虚构）一道题" },
    commPubRights: { checked: true },
    commPubIncludeOriginals: { checked: true },
    commPubIncludeOneNote: { checked: true },
    commPubIncludeOneNoteRow: { classList: { contains: () => false } },
  };
  const context = {
    questionsData: [{ id: "one", documentId: "parent", stem: "（虚构）目标题" }, { id: "two", documentId: "parent", stem: "（虚构）同文件夹其他题" }],
    document: { getElementById: (id) => values[id] || null },
    openCommunityPublishModal: (target) => { context.communityPublishTarget = target; },
    showToast: (message) => notices.push(message),
    setCommPublishStatus: (message) => { if (message) notices.push(message); },
    getCommunitySiteUrl: () => "https://example.invalid",
    API_BASE: "",
    activeCommUploadTaskId: "",
    commUploadPollTimer: null,
    clearTimeout() {},
    setTimeout() { return 1; },
    fetch: async (_url, options) => {
      requests.push(JSON.parse(options.body));
      return { ok: true, json: async () => ({ ok: true, task_id: "fake-task" }) };
    },
  };
  vm.createContext(context);
  for (const name of ["publishQuestionToCommunity", "submitCommunityPublish"]) {
    const pattern = new RegExp(`(?:async )?function ${name}\\([^]*?(?=\\n    (?:async )?function )`);
    const code = source.match(pattern)?.[0];
    assert.ok(code, `找不到 ${name}`);
    vm.runInContext(code, context);
  }
  return { context, requests, notices };
}

test("单题入口发送唯一题目 ID，附带所选笔记，不改为母资料 ID", async () => {
  const { context, requests, notices } = load();
  context.publishQuestionToCommunity("one");
  await context.submitCommunityPublish();
  assert.equal(requests.length, 1);
  assert.deepEqual(requests[0].publication_ids, ["one"]);
  assert.equal(requests[0].share_kind, "question");
  assert.equal(requests[0].include_onenote, true);
  assert.equal(requests[0].include_originals, false);
  assert.equal(requests[0].paper_ids, undefined);
  assert.deepEqual(notices, []);
});

test("无效单题范围在上传前停止", async () => {
  const { context, requests, notices } = load();
  context.communityPublishTarget = { mode: "question", publicationIds: ["one", "two"] };
  await context.submitCommunityPublish();
  assert.equal(requests.length, 0);
  assert.ok(notices[0].includes("只能上传一道题"));
});
