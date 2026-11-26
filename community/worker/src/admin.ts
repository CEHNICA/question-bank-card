import { Hono } from "hono";
import type { AppBindings } from "./env.ts";
import { loadPack, maxTotalR2Bytes, activeR2Bytes, packBuyerCount, purgePackStorage } from "./packs.ts";
import { fail, intParam, nowIso, readJson, requireAdmin, text } from "./util.ts";

export const adminRoutes = new Hono<AppBindings>();

adminRoutes.use("*", async (c, next) => {
  requireAdmin(c);
  await next();
});

adminRoutes.get("/storage", async (c) => {
  const usedBytes = await activeR2Bytes(c.env);
  const maxBytes = maxTotalR2Bytes(c.env);
  const counts = await c.env.DB.prepare(
    `SELECT
       SUM(CASE WHEN r2_purged_at IS NULL THEN 1 ELSE 0 END) AS active_packs,
       SUM(CASE WHEN status = 'uploading' THEN 1 ELSE 0 END) AS uploading_packs,
       SUM(CASE WHEN r2_purged_at IS NULL AND status IN ('rejected', 'withdrawn', 'taken_down')
                 AND NOT EXISTS (SELECT 1 FROM entitlements e WHERE e.pack_id = packs.id)
                THEN 1 ELSE 0 END) AS recoverable_packs,
       COALESCE(SUM(CASE WHEN r2_purged_at IS NULL AND status IN ('rejected', 'withdrawn', 'taken_down')
                          AND NOT EXISTS (SELECT 1 FROM entitlements e WHERE e.pack_id = packs.id)
                         THEN size ELSE 0 END), 0) AS recoverable_bytes
     FROM packs`,
  ).first<Record<string, number | null>>();
  return c.json({
    used_bytes: usedBytes,
    max_total_bytes: maxBytes,
    active_packs: Number(counts?.active_packs ?? 0),
    uploading_packs: Number(counts?.uploading_packs ?? 0),
    recoverable_packs: Number(counts?.recoverable_packs ?? 0),
    recoverable_bytes: Number(counts?.recoverable_bytes ?? 0),
  });
});

adminRoutes.get("/packs", async (c) => {
  const status = text(c.req.query("status"), 20) || "pending";
  const rows = await c.env.DB.prepare(
    `SELECT p.id, p.title, p.status, p.stage, p.subject, p.year, p.price_cents, p.originality, p.question_count,
            p.paper_count,
            p.include_originals, p.size, p.r2_purged_at, p.created_at, p.review_note, u.username AS author_username,
            u.display_name AS author_name,
            (SELECT COUNT(*) FROM entitlements e WHERE e.pack_id = p.id) AS buyers
       FROM packs p JOIN users u ON u.id = p.author_id
      WHERE p.status = ? ORDER BY p.created_at ASC LIMIT 200`,
  )
    .bind(status)
    .all();
  return c.json({ packs: rows.results ?? [] });
});

adminRoutes.post("/packs/:id/purge", async (c) => {
  const pack = await loadPack(c.env, c.req.param("id"));
  if (!pack) fail(404, "没有这份题包。");
  if (pack.r2_purged_at) return c.json({ ok: true, purged: false });
  if (pack.status === "pending" || pack.status === "approved") {
    fail(400, "请先驳回或下架后再清理存储文件。");
  }
  const buyers = await packBuyerCount(c.env, pack.id);
  const body: Record<string, unknown> = await readJson(c).catch(() => ({}));
  if (buyers > 0 && !(pack.status === "taken_down" && body.force === true)) {
    fail(409, "已有买家拥有这份题包，不能直接删除文件。");
  }
  await purgePackStorage(c.env, pack);
  return c.json({ ok: true, purged: true });
});

const REVIEW_ACTIONS: Record<string, string> = {
  approve: "approved",
  reject: "rejected",
  takedown: "taken_down",
  restore: "approved",
};

adminRoutes.post("/packs/:id/review", async (c) => {
  const admin = requireAdmin(c);
  const body = await readJson(c);
  const action = String(body.action ?? "");
  const status = REVIEW_ACTIONS[action];
  if (!status) fail(400, "审核动作只能是通过、驳回、下架或恢复。");
  const note = text(body.note, 500);
  if ((action === "reject" || action === "takedown") && note.length < 2) fail(400, "驳回或下架请写一句理由，作者能看到。");
  const result = await c.env.DB.prepare(
    "UPDATE packs SET status = ?, review_note = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
  )
    .bind(status, note, admin.id, nowIso(), nowIso(), c.req.param("id"))
    .run();
  if (!Number(result.meta?.changes ?? 0)) fail(404, "没有这份题包。");
  return c.json({ ok: true, status });
});

adminRoutes.get("/reports", async (c) => {
  const status = text(c.req.query("status"), 20) || "open";
  const rows = await c.env.DB.prepare(
    `SELECT r.id, r.target_type, r.target_id, r.reason, r.status, r.resolution_note, r.created_at,
            u.username AS reporter_username,
            CASE r.target_type
              WHEN 'pack' THEN (SELECT title FROM packs WHERE id = r.target_id)
              WHEN 'post' THEN (SELECT title FROM posts WHERE id = CAST(r.target_id AS INTEGER))
              ELSE (SELECT substr(body, 1, 80) FROM comments WHERE id = CAST(r.target_id AS INTEGER))
            END AS target_title
       FROM reports r JOIN users u ON u.id = r.reporter_id
      WHERE r.status = ? ORDER BY r.created_at ASC LIMIT 200`,
  )
    .bind(status)
    .all();
  return c.json({ reports: rows.results ?? [] });
});

adminRoutes.post("/reports/:id/resolve", async (c) => {
  const admin = requireAdmin(c);
  const id = intParam(c.req.param("id"), 0, 0, Number.MAX_SAFE_INTEGER);
  const body = await readJson(c);
  const action = String(body.action ?? "");
  if (action !== "dismiss" && action !== "takedown") fail(400, "处理方式只能是驳回举报或下架内容。");
  const note = text(body.note, 500);
  const report = await c.env.DB.prepare("SELECT id, target_type, target_id, status FROM reports WHERE id = ?")
    .bind(id)
    .first<{ id: number; target_type: string; target_id: string; status: string }>();
  if (!report) fail(404, "没有这条举报。");
  const now = nowIso();
  const statements = [];
  if (action === "takedown") {
    if (report.target_type === "pack") {
      statements.push(
        c.env.DB.prepare(
          "UPDATE packs SET status = 'taken_down', review_note = ?, reviewed_by = ?, reviewed_at = ?, updated_at = ? WHERE id = ?",
        ).bind(note || "因举报下架", admin.id, now, now, report.target_id),
      );
    } else if (report.target_type === "post") {
      statements.push(c.env.DB.prepare("UPDATE posts SET status = 'hidden' WHERE id = ?").bind(Number(report.target_id)));
    } else {
      statements.push(c.env.DB.prepare("UPDATE comments SET status = 'hidden' WHERE id = ?").bind(Number(report.target_id)));
    }
  }
  // 同一目标的其他未处理举报一起结掉，免得管理员重复处理。
  statements.push(
    c.env.DB.prepare(
      `UPDATE reports SET status = ?, resolution_note = ?, resolved_by = ?, resolved_at = ?
        WHERE status = 'open' AND target_type = ? AND target_id = ?`,
    ).bind(action === "takedown" ? "resolved" : "dismissed", note, admin.id, now, report.target_type, report.target_id),
  );
  await c.env.DB.batch(statements);
  return c.json({ ok: true });
});

adminRoutes.get("/users", async (c) => {
  const q = text(c.req.query("q"), 40);
  const rows = await c.env.DB.prepare(
    `SELECT id, username, display_name, role, banned_at, created_at,
            pay_note != '' OR pay_qr_key IS NOT NULL AS has_payment FROM users
      WHERE (? = '' OR username LIKE ?) ORDER BY created_at DESC LIMIT 100`,
  )
    .bind(q, `%${q}%`)
    .all();
  return c.json({ users: rows.results ?? [] });
});

adminRoutes.post("/users/:id/ban", async (c) => {
  const admin = requireAdmin(c);
  const id = intParam(c.req.param("id"), 0, 0, Number.MAX_SAFE_INTEGER);
  if (id === admin.id) fail(400, "不能停用自己。");
  const body = await readJson(c);
  const banned = Boolean(body.banned);
  const statements = [
    c.env.DB.prepare("UPDATE users SET banned_at = ? WHERE id = ? AND role != 'admin'").bind(banned ? nowIso() : null, id),
  ];
  if (banned) statements.push(c.env.DB.prepare("DELETE FROM sessions WHERE user_id = ?").bind(id));
  const [result] = await c.env.DB.batch(statements);
  if (!Number(result.meta?.changes ?? 0)) fail(404, "没有这个用户，或者对方是管理员。");
  return c.json({ ok: true, banned });
});

// 收款码被举报（冒用、引流）时清掉；付费题包随之没法付款，需要另外下架。
adminRoutes.post("/users/:id/clear-payment", async (c) => {
  const id = intParam(c.req.param("id"), 0, 0, Number.MAX_SAFE_INTEGER);
  const row = await c.env.DB.prepare("SELECT pay_qr_key FROM users WHERE id = ?")
    .bind(id)
    .first<{ pay_qr_key: string | null }>();
  if (!row) fail(404, "没有这个用户。");
  await c.env.DB.prepare("UPDATE users SET pay_note = '', pay_qr_key = NULL WHERE id = ?").bind(id).run();
  if (row.pay_qr_key) await c.env.PACKS.delete(row.pay_qr_key);
  return c.json({ ok: true });
});
