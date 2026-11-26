import { Hono } from "hono";
import type { AppBindings, Env } from "./env.ts";
import { fail, hit, intParam, nowIso, randomBytes, bytesToBase64Url, rateLimited, readJson, requireUser, text } from "./util.ts";
import { hasEntitlement, loadPack } from "./packs.ts";

export const MIN_PAID_CENTS = 100;
export const MAX_PRICE_CENTS = 99_900;
const PAY_NOTE_MAX = 300;
const PAY_QR_MAX_BYTES = 512 * 1024;
const BUYER_NOTE_MAX = 100;
const MAX_PENDING_PER_BUYER = 10;

/** 「12」「12.5」「12.50」元 → 分；0 是免费。 */
export function parsePriceYuan(raw: unknown, fallback?: number): number {
  const value = String(raw ?? "").trim();
  if (!value) {
    if (fallback !== undefined) return fallback;
    return 0;
  }
  if (!/^\d{1,4}(\.\d{1,2})?$/.test(value)) fail(400, "价格请填元，最多两位小数，如 9.9。");
  const [whole, fraction = ""] = value.split(".");
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (cents > 0 && cents < MIN_PAID_CENTS) fail(400, "付费题包最低 1 元；想免费分享就填 0。");
  if (cents > MAX_PRICE_CENTS) fail(400, "单个题包最高 999 元。");
  return cents;
}

export async function authorPayment(env: Env, authorId: number) {
  const row = await env.DB.prepare("SELECT pay_note, pay_qr_key FROM users WHERE id = ?")
    .bind(authorId)
    .first<{ pay_note: string; pay_qr_key: string | null }>();
  return { note: row?.pay_note ?? "", has_qr: Boolean(row?.pay_qr_key) };
}

/** 设付费价前的三道关：原创、不带原卷、作者填过收款方式。 */
export async function assertCanSell(
  env: Env,
  pack: { author_id: number; originality: string; include_originals: number | boolean },
) {
  if (pack.originality !== "original") {
    fail(400, "付费题包只能是你本人原创的题目。整理、汇编的教辅或真题请免费分享。");
  }
  if (pack.include_originals) fail(400, "付费题包不能附带原卷文件，请重新导出不带原卷的题包。");
  const payment = await authorPayment(env, pack.author_id);
  if (!payment.note && !payment.has_qr) {
    fail(400, "先在「我的 → 收款方式」填写收款说明或上传收款码，买家才知道怎么付款。");
  }
}

function sniffImage(bytes: Uint8Array): { ext: string; contentType: string } | null {
  if (bytes.length > 8 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47) {
    return { ext: "png", contentType: "image/png" };
  }
  if (bytes.length > 3 && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff) {
    return { ext: "jpg", contentType: "image/jpeg" };
  }
  const ascii = (start: number, end: number) => String.fromCharCode(...bytes.slice(start, end));
  if (bytes.length > 12 && ascii(0, 4) === "RIFF" && ascii(8, 12) === "WEBP") {
    return { ext: "webp", contentType: "image/webp" };
  }
  return null;
}

async function sellingPackCount(env: Env, authorId: number): Promise<number> {
  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM packs WHERE author_id = ? AND price_cents > 0 AND status IN ('pending', 'approved')",
  )
    .bind(authorId)
    .first<{ n: number }>();
  return Number(row?.n ?? 0);
}

const STILL_SELLING = "你还有付费题包在售，先把它们改成免费或撤回，再清空收款方式。";

export const salesRoutes = new Hono<AppBindings>();

salesRoutes.get("/me/payment", async (c) => {
  const user = requireUser(c);
  return c.json(await authorPayment(c.env, user.id));
});

salesRoutes.post("/me/payment", async (c) => {
  const user = requireUser(c);
  const body = await readJson(c);
  const note = text(body.note, PAY_NOTE_MAX);
  const current = await authorPayment(c.env, user.id);
  if (!note && !current.has_qr && (await sellingPackCount(c.env, user.id))) fail(400, STILL_SELLING);
  await c.env.DB.prepare("UPDATE users SET pay_note = ? WHERE id = ?").bind(note, user.id).run();
  return c.json({ ok: true });
});

salesRoutes.post("/me/payment/qr", async (c) => {
  const user = requireUser(c);
  const form = await c.req.parseBody();
  const file = form.file;
  if (!(file instanceof File)) fail(400, "请选择一张收款码图片。");
  if (file.size > PAY_QR_MAX_BYTES) fail(413, "收款码图片不能超过 512 KB，请截图后裁剪一下。");
  const bytes = new Uint8Array(await file.arrayBuffer());
  const kind = sniffImage(bytes);
  if (!kind) fail(400, "收款码只支持 PNG、JPG 或 WebP 图片。");
  const old = await c.env.DB.prepare("SELECT pay_qr_key FROM users WHERE id = ?")
    .bind(user.id)
    .first<{ pay_qr_key: string | null }>();
  const key = `pay-qr/${user.id}-${bytesToBase64Url(randomBytes(6))}.${kind.ext}`;
  await c.env.PACKS.put(key, bytes, { httpMetadata: { contentType: kind.contentType } });
  await c.env.DB.prepare("UPDATE users SET pay_qr_key = ? WHERE id = ?").bind(key, user.id).run();
  if (old?.pay_qr_key) await c.env.PACKS.delete(old.pay_qr_key);
  return c.json({ ok: true }, 201);
});

salesRoutes.delete("/me/payment/qr", async (c) => {
  const user = requireUser(c);
  const row = await c.env.DB.prepare("SELECT pay_note, pay_qr_key FROM users WHERE id = ?")
    .bind(user.id)
    .first<{ pay_note: string; pay_qr_key: string | null }>();
  if (!row?.pay_qr_key) return c.json({ ok: true });
  if (!row.pay_note && (await sellingPackCount(c.env, user.id))) fail(400, STILL_SELLING);
  await c.env.DB.prepare("UPDATE users SET pay_qr_key = NULL WHERE id = ?").bind(user.id).run();
  await c.env.PACKS.delete(row.pay_qr_key);
  return c.json({ ok: true });
});

// 收款码只给登录用户看，免得被爬去做别的用途。
salesRoutes.get("/users/:username/pay-qr", async (c) => {
  requireUser(c);
  const row = await c.env.DB.prepare("SELECT pay_qr_key FROM users WHERE username = ? AND banned_at IS NULL")
    .bind(c.req.param("username"))
    .first<{ pay_qr_key: string | null }>();
  if (!row?.pay_qr_key) fail(404, "对方没有上传收款码。");
  const object = await c.env.PACKS.get(row.pay_qr_key);
  if (!object) fail(404, "对方没有上传收款码。");
  return new Response(object.body as unknown as ReadableStream, {
    headers: {
      "Content-Type": object.httpMetadata?.contentType || "image/png",
      "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
});

salesRoutes.post("/packs/:id/purchase", async (c) => {
  const user = requireUser(c);
  const pack = await loadPack(c.env, c.req.param("id"));
  if (!pack || pack.status !== "approved") fail(404, "没有这份题包。");
  if (pack.price_cents <= 0) fail(400, "这是免费题包，直接下载就行。");
  if (pack.author_id === user.id) fail(400, "这是你自己的题包。");
  if (await hasEntitlement(c.env, user.id, pack.id)) return c.json({ already_owned: true });
  const body = await readJson(c);
  const note = text(body.note, BUYER_NOTE_MAX);
  if (note.length < 2) fail(400, "写一句付款信息，方便作者核对，如：微信转账，昵称小王，14:05。");
  const limiterKey = `purchase:${user.id}`;
  if (await rateLimited(c.env, limiterKey, 20, 24 * 3600 * 1000)) fail(429, "今天提交的付款通知太多了，明天再试。");
  const pending = await c.env.DB.prepare(
    "SELECT COUNT(*) AS n, SUM(pack_id = ?) AS here FROM purchase_requests WHERE buyer_id = ? AND status = 'pending'",
  )
    .bind(pack.id, user.id)
    .first<{ n: number; here: number | null }>();
  if (Number(pending?.here ?? 0)) fail(409, "你已经通知过作者了，请等作者核对到账。");
  if (Number(pending?.n ?? 0) >= MAX_PENDING_PER_BUYER) fail(429, "你还有很多付款通知没处理完，等作者确认后再提交新的。");
  await hit(c.env, limiterKey);
  const row = await c.env.DB.prepare(
    `INSERT INTO purchase_requests (pack_id, buyer_id, author_id, price_cents, buyer_note)
     VALUES (?, ?, ?, ?, ?) RETURNING id`,
  )
    .bind(pack.id, user.id, pack.author_id, pack.price_cents, note)
    .first<{ id: number }>();
  return c.json({ id: row!.id, status: "pending" }, 201);
});

type RequestRow = {
  id: number;
  pack_id: string;
  buyer_id: number;
  author_id: number;
  status: string;
};

async function loadRequest(env: Env, id: string): Promise<RequestRow | null> {
  return (
    (await env.DB.prepare("SELECT id, pack_id, buyer_id, author_id, status FROM purchase_requests WHERE id = ?")
      .bind(intParam(id, 0, 0, Number.MAX_SAFE_INTEGER))
      .first<RequestRow>()) ?? null
  );
}

salesRoutes.post("/purchase-requests/:id/cancel", async (c) => {
  const user = requireUser(c);
  const request = await loadRequest(c.env, c.req.param("id"));
  if (!request || request.buyer_id !== user.id) fail(404, "没有这条付款通知。");
  if (request.status !== "pending") fail(400, "作者已经处理过这条通知了。");
  await c.env.DB.prepare(
    "UPDATE purchase_requests SET status = 'cancelled', decided_at = ? WHERE id = ? AND status = 'pending'",
  )
    .bind(nowIso(), request.id)
    .run();
  return c.json({ ok: true });
});

salesRoutes.post("/purchase-requests/:id/grant", async (c) => {
  const user = requireUser(c);
  const request = await loadRequest(c.env, c.req.param("id"));
  if (!request || request.author_id !== user.id) fail(404, "没有这条付款通知。");
  if (request.status !== "pending") fail(400, "这条通知已经处理过了。");
  const pack = await loadPack(c.env, request.pack_id);
  if (!pack || pack.status === "taken_down" || pack.status === "rejected") {
    fail(400, "这份题包已下架，不能再开通。如已收款请退还买家。");
  }
  const now = nowIso();
  // 先把通知改成已开通，再按「已开通且是这一刻」插入权限：同一条通知点两次也只开通一次。
  const [claim] = await c.env.DB.batch([
    c.env.DB.prepare(
      "UPDATE purchase_requests SET status = 'granted', decided_at = ? WHERE id = ? AND status = 'pending'",
    ).bind(now, request.id),
    c.env.DB.prepare(
      `INSERT OR IGNORE INTO entitlements (user_id, pack_id, source, request_id, price_cents, created_at)
       SELECT buyer_id, pack_id, 'purchase', id, price_cents, ? FROM purchase_requests
        WHERE id = ? AND status = 'granted' AND decided_at = ?`,
    ).bind(now, request.id, now),
  ]);
  if (Number(claim.meta?.changes ?? 0) !== 1) fail(409, "这条通知刚刚被处理过了。");
  return c.json({ ok: true });
});

salesRoutes.post("/purchase-requests/:id/decline", async (c) => {
  const user = requireUser(c);
  const request = await loadRequest(c.env, c.req.param("id"));
  if (!request || request.author_id !== user.id) fail(404, "没有这条付款通知。");
  if (request.status !== "pending") fail(400, "这条通知已经处理过了。");
  const body = await readJson(c);
  const reason = text(body.reason, 200);
  if (reason.length < 2) fail(400, "写一句原因，买家能看到，如：没有查到这笔转账。");
  await c.env.DB.prepare(
    `UPDATE purchase_requests SET status = 'declined', decline_reason = ?, decided_at = ?
      WHERE id = ? AND status = 'pending'`,
  )
    .bind(reason, nowIso(), request.id)
    .run();
  return c.json({ ok: true });
});

salesRoutes.get("/me/purchase-requests", async (c) => {
  const user = requireUser(c);
  const incoming = await c.env.DB.prepare(
    `SELECT r.id, r.pack_id, p.title AS pack_title, r.price_cents, r.buyer_note, r.status, r.decline_reason,
            r.created_at, r.decided_at, u.username AS buyer_username, u.display_name AS buyer_name
       FROM purchase_requests r JOIN packs p ON p.id = r.pack_id JOIN users u ON u.id = r.buyer_id
      WHERE r.author_id = ? AND r.status != 'cancelled'
      ORDER BY r.status = 'pending' DESC, r.created_at DESC LIMIT 200`,
  )
    .bind(user.id)
    .all();
  const outgoing = await c.env.DB.prepare(
    `SELECT r.id, r.pack_id, p.title AS pack_title, r.price_cents, r.buyer_note, r.status, r.decline_reason,
            r.created_at, r.decided_at, u.username AS author_username, u.display_name AS author_name
       FROM purchase_requests r JOIN packs p ON p.id = r.pack_id JOIN users u ON u.id = r.author_id
      WHERE r.buyer_id = ? ORDER BY r.created_at DESC LIMIT 100`,
  )
    .bind(user.id)
    .all();
  return c.json({ incoming: incoming.results ?? [], outgoing: outgoing.results ?? [] });
});

export async function pendingSalesCount(env: Env, authorId: number): Promise<number> {
  const row = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM purchase_requests WHERE author_id = ? AND status = 'pending'",
  )
    .bind(authorId)
    .first<{ n: number }>();
  return Number(row?.n ?? 0);
}
