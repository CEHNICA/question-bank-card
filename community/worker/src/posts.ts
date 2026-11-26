import { Hono } from "hono";
import type { AppBindings } from "./env.ts";
import { clientIp, fail, hit, intParam, nowIso, rateLimited, readJson, requireUser, text } from "./util.ts";
import { fileReport } from "./packs.ts";

export const postRoutes = new Hono<AppBindings>();

postRoutes.get("/posts", async (c) => {
  const page = intParam(c.req.query("page"), 1, 1, 1000);
  const packId = text(c.req.query("pack"), 40);
  const q = text(c.req.query("q"), 60);
  const sort = c.req.query("sort") === "hot" ? "p.like_count DESC, p.created_at DESC" : "p.created_at DESC";
  const pageSize = 20;
  const where = ["p.status = 'visible'"];
  const params: unknown[] = [];
  if (packId) {
    where.push("p.pack_id = ?");
    params.push(packId);
  }
  if (q) {
    where.push("(p.title LIKE ? OR p.body LIKE ?)");
    params.push(`%${q}%`, `%${q}%`);
  }
  const rows = await c.env.DB.prepare(
    `SELECT p.id, p.title, substr(p.body, 1, 200) AS excerpt, p.pack_id, p.like_count, p.comment_count,
            p.created_at, u.username AS author_username, u.display_name AS author_name, k.title AS pack_title
       FROM posts p JOIN users u ON u.id = p.author_id
       LEFT JOIN packs k ON k.id = p.pack_id AND k.status = 'approved'
      WHERE ${where.join(" AND ")}
      ORDER BY ${sort} LIMIT ? OFFSET ?`,
  )
    .bind(...params, pageSize + 1, (page - 1) * pageSize)
    .all();
  const results = rows.results ?? [];
  return c.json({ posts: results.slice(0, pageSize), has_more: results.length > pageSize, page });
});

async function checkPostingAllowed(c: Parameters<typeof requireUser>[0], kind: string, limit: number) {
  const user = requireUser(c);
  const key = `${kind}:${user.id}`;
  if (await rateLimited(c.env, key, limit, 3600 * 1000)) fail(429, "发得太快了，休息一会儿再来。");
  if (await rateLimited(c.env, `${kind}-ip:${clientIp(c)}`, limit * 2, 3600 * 1000)) {
    fail(429, "发得太快了，休息一会儿再来。");
  }
  return { user, key };
}

postRoutes.post("/posts", async (c) => {
  const { user, key } = await checkPostingAllowed(c, "post", 10);
  const body = await readJson(c);
  const title = text(body.title, 100);
  const content = text(body.body, 20000);
  if (title.length < 2) fail(400, "请填一个标题。");
  if (content.length < 5) fail(400, "正文太短了。");
  let packId: string | null = text(body.pack_id, 40) || null;
  if (packId) {
    const pack = await c.env.DB.prepare("SELECT id FROM packs WHERE id = ? AND status = 'approved'").bind(packId).first();
    if (!pack) packId = null;
  }
  const row = await c.env.DB.prepare(
    "INSERT INTO posts (author_id, title, body, pack_id) VALUES (?, ?, ?, ?) RETURNING id",
  )
    .bind(user.id, title, content, packId)
    .first<{ id: number }>();
  await hit(c.env, key);
  await hit(c.env, `post-ip:${clientIp(c)}`);
  return c.json({ id: row!.id }, 201);
});

postRoutes.get("/posts/:id", async (c) => {
  const user = c.get("user");
  const id = intParam(c.req.param("id"), 0, 0, Number.MAX_SAFE_INTEGER);
  const post = await c.env.DB.prepare(
    `SELECT p.*, u.username AS author_username, u.display_name AS author_name, k.title AS pack_title,
            k.status AS pack_status
       FROM posts p JOIN users u ON u.id = p.author_id LEFT JOIN packs k ON k.id = p.pack_id
      WHERE p.id = ?`,
  )
    .bind(id)
    .first<Record<string, unknown>>();
  const isAdmin = user?.role === "admin";
  if (!post || (post.status !== "visible" && !isAdmin && user?.id !== post.author_id)) fail(404, "没有这篇心得。");
  if (post.pack_status !== "approved") {
    post.pack_id = null;
    post.pack_title = null;
  }
  delete post.pack_status;
  const comments = await c.env.DB.prepare(
    `SELECT c.id, c.body, c.created_at, c.author_id, u.username AS author_username, u.display_name AS author_name
       FROM comments c JOIN users u ON u.id = c.author_id
      WHERE c.post_id = ? AND c.status = 'visible' ORDER BY c.created_at ASC LIMIT 500`,
  )
    .bind(id)
    .all();
  const liked = user
    ? Boolean(await c.env.DB.prepare("SELECT 1 FROM likes WHERE post_id = ? AND user_id = ?").bind(id, user.id).first())
    : false;
  return c.json({
    post,
    comments: comments.results ?? [],
    viewer: { liked, can_manage: Boolean(user && (isAdmin || user.id === post.author_id)) },
  });
});

async function loadOwnPost(c: Parameters<typeof requireUser>[0]) {
  const user = requireUser(c);
  const id = intParam(c.req.param("id"), 0, 0, Number.MAX_SAFE_INTEGER);
  const post = await c.env.DB.prepare("SELECT id, author_id, status FROM posts WHERE id = ?")
    .bind(id)
    .first<{ id: number; author_id: number; status: string }>();
  if (!post || (post.author_id !== user.id && user.role !== "admin")) fail(404, "没有这篇心得。");
  return { user, post };
}

postRoutes.patch("/posts/:id", async (c) => {
  const { post } = await loadOwnPost(c);
  const body = await readJson(c);
  const title = text(body.title, 100);
  const content = text(body.body, 20000);
  if (title.length < 2 || content.length < 5) fail(400, "标题或正文太短了。");
  await c.env.DB.prepare("UPDATE posts SET title = ?, body = ?, updated_at = ? WHERE id = ?")
    .bind(title, content, nowIso(), post.id)
    .run();
  return c.json({ ok: true });
});

postRoutes.delete("/posts/:id", async (c) => {
  const { post } = await loadOwnPost(c);
  await c.env.DB.prepare("UPDATE posts SET status = 'hidden', updated_at = ? WHERE id = ?")
    .bind(nowIso(), post.id)
    .run();
  return c.json({ ok: true });
});

postRoutes.post("/posts/:id/comments", async (c) => {
  const { user, key } = await checkPostingAllowed(c, "comment", 60);
  const id = intParam(c.req.param("id"), 0, 0, Number.MAX_SAFE_INTEGER);
  const body = await readJson(c);
  const content = text(body.body, 3000);
  if (content.length < 1) fail(400, "评论不能是空的。");
  const post = await c.env.DB.prepare("SELECT id FROM posts WHERE id = ? AND status = 'visible'").bind(id).first();
  if (!post) fail(404, "没有这篇心得。");
  const row = await c.env.DB.prepare(
    "INSERT INTO comments (post_id, author_id, body) VALUES (?, ?, ?) RETURNING id, created_at",
  )
    .bind(id, user.id, content)
    .first<{ id: number; created_at: string }>();
  await c.env.DB.prepare(
    "UPDATE posts SET comment_count = (SELECT COUNT(*) FROM comments WHERE post_id = ? AND status = 'visible') WHERE id = ?",
  )
    .bind(id, id)
    .run();
  await hit(c.env, key);
  await hit(c.env, `comment-ip:${clientIp(c)}`);
  return c.json({ id: row!.id, created_at: row!.created_at }, 201);
});

postRoutes.delete("/comments/:id", async (c) => {
  const user = requireUser(c);
  const id = intParam(c.req.param("id"), 0, 0, Number.MAX_SAFE_INTEGER);
  const comment = await c.env.DB.prepare("SELECT id, post_id, author_id FROM comments WHERE id = ?")
    .bind(id)
    .first<{ id: number; post_id: number; author_id: number }>();
  if (!comment || (comment.author_id !== user.id && user.role !== "admin")) fail(404, "没有这条评论。");
  await c.env.DB.batch([
    c.env.DB.prepare("UPDATE comments SET status = 'hidden' WHERE id = ?").bind(id),
    c.env.DB.prepare(
      "UPDATE posts SET comment_count = (SELECT COUNT(*) FROM comments WHERE post_id = ? AND status = 'visible') WHERE id = ?",
    ).bind(comment.post_id, comment.post_id),
  ]);
  return c.json({ ok: true });
});

postRoutes.post("/posts/:id/like", async (c) => {
  const user = requireUser(c);
  const id = intParam(c.req.param("id"), 0, 0, Number.MAX_SAFE_INTEGER);
  const post = await c.env.DB.prepare("SELECT id FROM posts WHERE id = ? AND status = 'visible'").bind(id).first();
  if (!post) fail(404, "没有这篇心得。");
  const existing = await c.env.DB.prepare("SELECT 1 FROM likes WHERE post_id = ? AND user_id = ?")
    .bind(id, user.id)
    .first();
  const toggle = existing
    ? c.env.DB.prepare("DELETE FROM likes WHERE post_id = ? AND user_id = ?").bind(id, user.id)
    : c.env.DB.prepare("INSERT OR IGNORE INTO likes (post_id, user_id) VALUES (?, ?)").bind(id, user.id);
  await c.env.DB.batch([
    toggle,
    c.env.DB.prepare("UPDATE posts SET like_count = (SELECT COUNT(*) FROM likes WHERE post_id = ?) WHERE id = ?").bind(
      id,
      id,
    ),
  ]);
  const count = await c.env.DB.prepare("SELECT like_count FROM posts WHERE id = ?").bind(id).first<{ like_count: number }>();
  return c.json({ liked: !existing, like_count: count?.like_count ?? 0 });
});

postRoutes.post("/posts/:id/report", async (c) => {
  const id = intParam(c.req.param("id"), 0, 0, Number.MAX_SAFE_INTEGER);
  const post = await c.env.DB.prepare("SELECT id FROM posts WHERE id = ?").bind(id).first();
  if (!post) fail(404, "没有这篇心得。");
  return fileReport(c, "post", String(id));
});

postRoutes.post("/comments/:id/report", async (c) => {
  const id = intParam(c.req.param("id"), 0, 0, Number.MAX_SAFE_INTEGER);
  const comment = await c.env.DB.prepare("SELECT id FROM comments WHERE id = ?").bind(id).first();
  if (!comment) fail(404, "没有这条评论。");
  return fileReport(c, "comment", String(id));
});
