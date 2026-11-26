import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { Client, makeEnv, makePackZip, packForm, promoteAdmin, stubTurnstile } from "./helpers.ts";

let restore: () => void;
before(() => {
  restore = stubTurnstile();
});
after(() => restore());

async function setup() {
  const env = makeEnv();
  const author = new Client(env);
  await author.register("author1");
  const reader = new Client(env);
  await reader.register("reader1");
  const admin = new Client(env);
  await admin.register("admin1");
  promoteAdmin(env, "admin1");
  return { env, author, reader, admin, anonymous: new Client(env) };
}

test("posts with markdown body, attached pack, comments and likes", async () => {
  const { author, reader, admin, anonymous } = await setup();
  const pack = await author.json("POST", "/api/packs", packForm(makePackZip()));
  await admin.json("POST", `/api/admin/packs/${pack.data.id}/review`, { action: "approve" });

  const created = await author.json("POST", "/api/posts", {
    title: "（虚构）讲评心得",
    body: "# 小结\n\n第 1 题用到 $f(x)=x^2$。",
    pack_id: pack.data.id,
  });
  assert.equal(created.status, 201);
  const id = created.data.id;

  const list = await anonymous.json("GET", "/api/posts");
  assert.equal(list.data.posts[0].pack_title, "（虚构）高一期中题包");
  assert.equal((await anonymous.json("GET", `/api/posts?pack=${pack.data.id}`)).data.posts.length, 1);
  const packDetail = await anonymous.json("GET", `/api/packs/${pack.data.id}`);
  assert.equal(packDetail.data.posts[0].id, id, "题包页能看到挂着它的心得");

  assert.equal((await anonymous.json("POST", `/api/posts/${id}/comments`, { body: "匿名" })).status, 401);
  const comment = await reader.json("POST", `/api/posts/${id}/comments`, { body: "（虚构）很有用" });
  assert.equal(comment.status, 201);
  const like = await reader.json("POST", `/api/posts/${id}/like`, {});
  assert.deepEqual(like.data, { liked: true, like_count: 1 });
  const unlike = await reader.json("POST", `/api/posts/${id}/like`, {});
  assert.deepEqual(unlike.data, { liked: false, like_count: 0 });
  await reader.json("POST", `/api/posts/${id}/like`, {});

  const detail = await reader.json("GET", `/api/posts/${id}`);
  assert.equal(detail.data.post.body.includes("$f(x)=x^2$"), true);
  assert.equal(detail.data.post.comment_count, 1);
  assert.equal(detail.data.comments[0].body, "（虚构）很有用");
  assert.equal(detail.data.viewer.liked, true);
  assert.equal(detail.data.viewer.can_manage, false);
});

test("only the author or an admin can edit or hide a post", async () => {
  const { author, reader, admin, anonymous } = await setup();
  const { data } = await author.json("POST", "/api/posts", { title: "（虚构）标题", body: "（虚构）正文内容" });
  assert.equal((await reader.json("PATCH", `/api/posts/${data.id}`, { title: "改了", body: "被别人改了" })).status, 404);
  assert.equal((await author.json("PATCH", `/api/posts/${data.id}`, { title: "（虚构）新标题", body: "（虚构）新正文" })).status, 200);
  assert.equal((await admin.json("DELETE", `/api/posts/${data.id}`)).status, 200);
  assert.equal((await anonymous.json("GET", `/api/posts/${data.id}`)).status, 404);
  assert.equal((await anonymous.json("GET", "/api/posts")).data.posts.length, 0);
});

test("unapproved packs cannot be attached and comment reports can hide comments", async () => {
  const { author, reader, admin } = await setup();
  const pending = await author.json("POST", "/api/packs", packForm(makePackZip()));
  const post = await author.json("POST", "/api/posts", {
    title: "（虚构）挂待审包",
    body: "（虚构）正文内容",
    pack_id: pending.data.id,
  });
  assert.equal((await reader.json("GET", `/api/posts/${post.data.id}`)).data.post.pack_id, null);

  const comment = await reader.json("POST", `/api/posts/${post.data.id}/comments`, { body: "（虚构）不当言论" });
  assert.equal((await author.json("POST", `/api/comments/${comment.data.id}/report`, { reason: "（虚构）人身攻击" })).status, 201);
  const reports = await admin.json("GET", "/api/admin/reports");
  await admin.json("POST", `/api/admin/reports/${reports.data.reports[0].id}/resolve`, { action: "takedown" });
  const detail = await reader.json("GET", `/api/posts/${post.data.id}`);
  assert.equal(detail.data.comments.length, 0);
});
