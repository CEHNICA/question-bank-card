import { Hono } from "hono";
import type { AppBindings } from "./env.ts";
import { fail, hit, intParam, nowIso, randomBytes, rateLimited, readJson, requireUser, sha256Hex, text } from "./util.ts";
import { hasEntitlement, loadPack } from "./packs.ts";

// 去掉 0/O、1/I 这些容易看错的字符；16 位 × 5 bit = 80 bit，猜不出来。
const CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const MAX_BATCH = 200;
const MAX_UNUSED_PER_PACK = 5000;

export function generateCode(): string {
  const bytes = randomBytes(16);
  const chars = [...bytes].map((byte) => CODE_ALPHABET[byte & 31]).join("");
  return chars.match(/.{4}/g)!.join("-");
}

export function normalizeCode(raw: string): string {
  return raw.toUpperCase().replace(/[^A-Z0-9]/g, "");
}

export async function hashCode(code: string): Promise<string> {
  return sha256Hex(`tyj-redeem:${normalizeCode(code)}`);
}

export const redeemRoutes = new Hono<AppBindings>();

async function loadOwnPaidPack(c: Parameters<typeof requireUser>[0]) {
  const user = requireUser(c);
  const pack = await loadPack(c.env, c.req.param("id") ?? "");
  if (!pack || pack.author_id !== user.id) fail(404, "没有这份题包，或者它不是你发布的。");
  return { user, pack };
}

redeemRoutes.post("/packs/:id/codes", async (c) => {
  const { user, pack } = await loadOwnPaidPack(c);
  if (pack.price_cents <= 0) fail(400, "免费题包不需要兑换码。先在题包设置里填一个价格。");
  if (pack.status !== "approved" && pack.status !== "pending") fail(400, "这份题包已下架或撤回，不能再生成兑换码。");
  const body = await readJson(c);
  const count = intParam(body.count, 0, 0, MAX_BATCH);
  if (count < 1) fail(400, `一次生成 1 到 ${MAX_BATCH} 个。`);
  const note = text(body.note, 60);
  const unused = await c.env.DB.prepare(
    "SELECT COUNT(*) AS n FROM redeem_codes WHERE pack_id = ? AND used_by IS NULL AND revoked_at IS NULL",
  )
    .bind(pack.id)
    .first<{ n: number }>();
  if (Number(unused?.n ?? 0) + count > MAX_UNUSED_PER_PACK) {
    fail(400, `这份题包还没用掉的兑换码太多了（上限 ${MAX_UNUSED_PER_PACK} 个），先作废一些再生成。`);
  }
  const batchId = crypto.randomUUID().slice(0, 8);
  const codes: string[] = [];
  const statements = [];
  for (let i = 0; i < count; i++) {
    const code = generateCode();
    codes.push(code);
    statements.push(
      c.env.DB.prepare(
        "INSERT INTO redeem_codes (pack_id, code_hash, hint, batch_id, note, created_by) VALUES (?, ?, ?, ?, ?, ?)",
      ).bind(pack.id, await hashCode(code), code.slice(-4), batchId, note, user.id),
    );
  }
  await c.env.DB.batch(statements);
  return c.json({ batch_id: batchId, codes, notice: "兑换码只显示这一次，请马上复制保存。" }, 201);
});

redeemRoutes.get("/packs/:id/codes", async (c) => {
  const { pack } = await loadOwnPaidPack(c);
  const rows = await c.env.DB.prepare(
    `SELECT r.id, r.hint, r.batch_id, r.note, r.created_at, r.used_at, r.revoked_at,
            u.username AS used_by_username, u.display_name AS used_by_name
       FROM redeem_codes r LEFT JOIN users u ON u.id = r.used_by
      WHERE r.pack_id = ? ORDER BY r.created_at DESC, r.id DESC LIMIT 2000`,
  )
    .bind(pack.id)
    .all<Record<string, unknown>>();
  const codes = rows.results ?? [];
  const summary = {
    total: codes.length,
    used: codes.filter((row) => row.used_at).length,
    revoked: codes.filter((row) => row.revoked_at).length,
  };
  return c.json({ codes, summary: { ...summary, available: summary.total - summary.used - summary.revoked } });
});

redeemRoutes.post("/packs/:id/codes/revoke", async (c) => {
  const { pack } = await loadOwnPaidPack(c);
  const body = await readJson(c);
  const ids = Array.isArray(body.code_ids)
    ? body.code_ids.map((value) => intParam(value, 0, 0, Number.MAX_SAFE_INTEGER)).filter(Boolean).slice(0, 2000)
    : [];
  const batchId = text(body.batch_id, 20);
  if (!ids.length && !batchId) fail(400, "请选要作废的兑换码或批次。");
  const now = nowIso();
  let changed = 0;
  if (batchId) {
    const result = await c.env.DB.prepare(
      "UPDATE redeem_codes SET revoked_at = ? WHERE pack_id = ? AND batch_id = ? AND used_by IS NULL AND revoked_at IS NULL",
    )
      .bind(now, pack.id, batchId)
      .run();
    changed += Number(result.meta?.changes ?? 0);
  }
  for (const id of ids) {
    const result = await c.env.DB.prepare(
      "UPDATE redeem_codes SET revoked_at = ? WHERE pack_id = ? AND id = ? AND used_by IS NULL AND revoked_at IS NULL",
    )
      .bind(now, pack.id, id)
      .run();
    changed += Number(result.meta?.changes ?? 0);
  }
  return c.json({ revoked: changed });
});

redeemRoutes.post("/redeem", async (c) => {
  const user = requireUser(c);
  const limiterKey = `redeem-fail:${user.id}`;
  if (await rateLimited(c.env, limiterKey, 10, 10 * 60 * 1000)) fail(429, "兑换码输错太多次了，10 分钟后再试。");
  const body = await readJson(c);
  const normalized = normalizeCode(String(body.code ?? ""));
  if (normalized.length !== 16) {
    await hit(c.env, limiterKey);
    fail(400, "兑换码是 16 位，形如 ABCD-EFGH-JKLM-NPQR。");
  }
  const code = await c.env.DB.prepare(
    "SELECT id, pack_id, used_by, revoked_at FROM redeem_codes WHERE code_hash = ?",
  )
    .bind(await hashCode(normalized))
    .first<{ id: number; pack_id: string; used_by: number | null; revoked_at: string | null }>();
  if (!code || code.revoked_at) {
    await hit(c.env, limiterKey);
    fail(400, "兑换码不对，或者已经被作者作废了。");
  }
  const pack = await loadPack(c.env, code.pack_id);
  if (!pack || pack.status === "taken_down" || pack.status === "rejected") {
    fail(400, "这份题包已经下架，兑换码不能用了，请联系作者。");
  }
  if (code.used_by === user.id || (await hasEntitlement(c.env, user.id, pack.id))) {
    return c.json({ pack_id: pack.id, title: pack.title, already_owned: true });
  }
  if (code.used_by) fail(400, "这个兑换码已经被别人用过了。");
  const now = nowIso();
  // 一个批处理里先占码、再按"占到的人是我"发放权限：两个人同时兑换同一个码，只有一个能拿到。
  const [claim] = await c.env.DB.batch([
    c.env.DB.prepare(
      "UPDATE redeem_codes SET used_by = ?, used_at = ? WHERE id = ? AND used_by IS NULL AND revoked_at IS NULL",
    ).bind(user.id, now, code.id),
    c.env.DB.prepare(
      `INSERT OR IGNORE INTO entitlements (user_id, pack_id, source, code_id, price_cents, created_at)
       SELECT r.used_by, r.pack_id, 'redeem', r.id, p.price_cents, ?
         FROM redeem_codes r JOIN packs p ON p.id = r.pack_id WHERE r.id = ? AND r.used_by = ?`,
    ).bind(now, code.id, user.id),
  ]);
  if (Number(claim.meta?.changes ?? 0) !== 1) fail(409, "这个兑换码刚刚被别人用掉了。");
  return c.json({ pack_id: pack.id, title: pack.title, already_owned: false });
});
