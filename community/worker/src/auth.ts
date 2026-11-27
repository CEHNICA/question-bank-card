import { Hono } from "hono";
import { deleteCookie, getCookie, setCookie } from "hono/cookie";
import type { AppBindings, Env, SessionUser } from "./env.ts";
import {
  type AppContext,
  base64UrlToBytes,
  bytesToBase64Url,
  clientIp,
  fail,
  hit,
  nowIso,
  randomBytes,
  rateLimited,
  readJson,
  requireUser,
  sha256Hex,
  text,
  timingSafeEqual,
} from "./util.ts";
import { pendingSalesCount } from "./sales.ts";
import { adminMaxPackMb } from "./packs.ts";

export const SESSION_COOKIE = "tyj_session";
const SESSION_DAYS = 30;
// Workers 的 WebCrypto 对 PBKDF2 迭代次数上限是 10 万。
const PBKDF2_ITERATIONS = 100_000;
const USERNAME_PATTERN = /^[A-Za-z0-9_\u4e00-\u9fa5]{2,20}$/;

async function pbkdf2(password: string, salt: Uint8Array, iterations: number): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, [
    "deriveBits",
  ]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations }, key, 256);
  return new Uint8Array(bits);
}

export async function hashPassword(password: string): Promise<string> {
  const salt = randomBytes(16);
  const hash = await pbkdf2(password, salt, PBKDF2_ITERATIONS);
  return `pbkdf2$${PBKDF2_ITERATIONS}$${bytesToBase64Url(salt)}$${bytesToBase64Url(hash)}`;
}

export async function verifyPassword(password: string, stored: string): Promise<boolean> {
  const [scheme, rawIterations, salt, expected] = stored.split("$");
  if (scheme !== "pbkdf2" || !salt || !expected) return false;
  const iterations = Number(rawIterations);
  if (!Number.isInteger(iterations) || iterations < 1 || iterations > PBKDF2_ITERATIONS) return false;
  const actual = await pbkdf2(password, base64UrlToBytes(salt), iterations);
  return timingSafeEqual(bytesToBase64Url(actual), expected);
}

export async function verifyTurnstile(env: Env, token: unknown, ip: string): Promise<void> {
  const secret = env.TURNSTILE_SECRET?.trim() || "";
  if (!secret) fail(503, "网站还没配置人机验证，暂时不能注册或登录。");
  const response = String(token ?? "").trim();
  if (!response) fail(400, "请先完成人机验证。");
  const form = new FormData();
  form.append("secret", secret);
  form.append("response", response);
  if (ip && ip !== "local") form.append("remoteip", ip);
  let ok = false;
  try {
    const result = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
      method: "POST",
      body: form,
    });
    const body = (await result.json()) as { success?: boolean; "error-codes"?: string[] };
    ok = Boolean(body.success);
    if (!ok) {
      console.error("turnstile verify failed", body["error-codes"] ?? []);
    }
  } catch {
    fail(503, "人机验证服务暂时连不上，请稍后再试。");
  }
  if (!ok) fail(400, "人机验证没通过，请刷新后再试。");
}

async function createSession(c: AppContext, userId: number): Promise<void> {
  const token = bytesToBase64Url(randomBytes(32));
  const expires = Date.now() + SESSION_DAYS * 86400 * 1000;
  await c.env.DB.prepare("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)")
    .bind(await sha256Hex(token), userId, expires)
    .run();
  setCookie(c, SESSION_COOKIE, token, {
    httpOnly: true,
    secure: new URL(c.req.url).protocol === "https:",
    sameSite: "Lax",
    path: "/",
    maxAge: SESSION_DAYS * 86400,
  });
}

export async function loadSessionUser(c: AppContext): Promise<SessionUser | null> {
  const token = getCookie(c, SESSION_COOKIE);
  if (!token) return null;
  const row = await c.env.DB.prepare(
    `SELECT u.id, u.username, u.display_name, u.role, u.banned_at
       FROM sessions s JOIN users u ON u.id = s.user_id
      WHERE s.token_hash = ? AND s.expires_at > ?`,
  )
    .bind(await sha256Hex(token), Date.now())
    .first<SessionUser>();
  return row ?? null;
}

export async function loadBearerUser(c: AppContext): Promise<SessionUser | null> {
  const auth = c.req.header("Authorization") || "";
  const match = /^Bearer\s+(.+)$/i.exec(auth.trim());
  if (!match) return null;
  const token = match[1].trim();
  if (!token) return null;
  const row = await c.env.DB.prepare(
    `SELECT u.id, u.username, u.display_name, u.role, u.banned_at, d.id AS token_id
       FROM desktop_tokens d JOIN users u ON u.id = d.user_id
      WHERE d.token_hash = ? AND d.revoked_at IS NULL`,
  )
    .bind(await sha256Hex(token))
    .first<SessionUser & { token_id: number }>();
  if (!row || row.banned_at) return null;
  await c.env.DB.prepare("UPDATE desktop_tokens SET last_used_at = ? WHERE id = ?")
    .bind(nowIso(), row.token_id)
    .run()
    .catch(() => null);
  return { id: row.id, username: row.username, display_name: row.display_name, role: row.role, banned_at: row.banned_at };
}

function publicUser(user: SessionUser) {
  return { id: user.id, username: user.username, display_name: user.display_name || user.username, role: user.role };
}

export const authRoutes = new Hono<AppBindings>();

authRoutes.get("/me", async (c) => {
  const user = c.get("user");
  if (!user || user.banned_at) return c.json({ user: null });
  const isAdmin = user.role === "admin";
  const maxPackMb = isAdmin ? adminMaxPackMb(c.env) : Number(c.env.MAX_PACK_MB || 50);
  return c.json({
    user: {
      ...publicUser(user),
      is_admin: isAdmin,
      unlimited_uploads: isAdmin,
      max_pack_mb: maxPackMb,
      pending_sales: await pendingSalesCount(c.env, user.id),
    },
  });
});

authRoutes.post("/register", async (c) => {
  const body = await readJson(c);
  const username = text(body.username, 40);
  const password = String(body.password ?? "");
  const displayName = text(body.display_name, 30) || username;
  if (!USERNAME_PATTERN.test(username)) fail(400, "用户名 2 到 20 个字，只能用中文、字母、数字和下划线。");
  if (password.length < 8 || password.length > 128) fail(400, "密码至少 8 位。");
  if (!body.agree_terms) fail(400, "请先阅读并同意社区规则。");
  const ip = clientIp(c);
  if (await rateLimited(c.env, `register:${ip}`, 5, 3600 * 1000)) fail(429, "注册太频繁了，过一小时再试。");
  await verifyTurnstile(c.env, body.turnstile_token, ip);
  const exists = await c.env.DB.prepare("SELECT id FROM users WHERE username = ?").bind(username).first();
  if (exists) fail(409, "这个用户名已经有人用了。");
  const result = await c.env.DB.prepare(
    "INSERT INTO users (username, display_name, password_hash) VALUES (?, ?, ?) RETURNING id",
  )
    .bind(username, displayName, await hashPassword(password))
    .first<{ id: number }>();
  await hit(c.env, `register:${ip}`);
  await createSession(c, Number(result!.id));
  return c.json({ user: { id: result!.id, username, display_name: displayName, role: "user" } }, 201);
});

authRoutes.post("/login", async (c) => {
  const body = await readJson(c);
  const username = text(body.username, 40);
  const password = String(body.password ?? "");
  const ip = clientIp(c);
  const limiterKey = `login:${ip}:${username.toLowerCase()}`;
  if (await rateLimited(c.env, limiterKey, 8, 15 * 60 * 1000)) fail(429, "密码错太多次了，15 分钟后再试。");
  await verifyTurnstile(c.env, body.turnstile_token, ip);
  const row = await c.env.DB.prepare(
    "SELECT id, username, display_name, role, banned_at, password_hash FROM users WHERE username = ?",
  )
    .bind(username)
    .first<SessionUser & { password_hash: string }>();
  if (!row || !(await verifyPassword(password, row.password_hash))) {
    await hit(c.env, limiterKey);
    fail(401, "用户名或密码不对。");
  }
  if (row.banned_at) fail(403, "这个账号已被停用。");
  await createSession(c, row.id);
  return c.json({ user: publicUser(row) });
});

authRoutes.post("/logout", async (c) => {
  const token = getCookie(c, SESSION_COOKIE);
  if (token) {
    await c.env.DB.prepare("DELETE FROM sessions WHERE token_hash = ?")
      .bind(await sha256Hex(token))
      .run();
  }
  deleteCookie(c, SESSION_COOKIE, { path: "/" });
  return c.json({ ok: true });
});

authRoutes.post("/me/profile", async (c) => {
  const user = requireUser(c);
  const body = await readJson(c);
  const displayName = text(body.display_name, 30) || user.username;
  const bio = text(body.bio, 300);
  await c.env.DB.prepare("UPDATE users SET bio = ?, display_name = ? WHERE id = ?").bind(bio, displayName, user.id).run();
  return c.json({ ok: true });
});

authRoutes.post("/me/password", async (c) => {
  const user = requireUser(c);
  const body = await readJson(c);
  const current = String(body.current_password ?? "");
  const next = String(body.new_password ?? "");
  if (next.length < 8 || next.length > 128) fail(400, "新密码至少 8 位。");
  const row = await c.env.DB.prepare("SELECT password_hash FROM users WHERE id = ?")
    .bind(user.id)
    .first<{ password_hash: string }>();
  if (!row || !(await verifyPassword(current, row.password_hash))) fail(400, "原密码不对。");
  await c.env.DB.batch([
    c.env.DB.prepare("UPDATE users SET password_hash = ? WHERE id = ?").bind(await hashPassword(next), user.id),
    c.env.DB.prepare("DELETE FROM sessions WHERE user_id = ?").bind(user.id),
  ]);
  await createSession(c, user.id);
  return c.json({ ok: true });
});

authRoutes.get("/users/:username", async (c) => {
  const row = await c.env.DB.prepare(
    "SELECT id, username, display_name, bio, role, created_at FROM users WHERE username = ? AND banned_at IS NULL",
  )
    .bind(c.req.param("username"))
    .first<Record<string, unknown>>();
  if (!row) fail(404, "没有这个用户。");
  const packs = await c.env.DB.prepare(
    `SELECT id, title, stage, subject, year, price_cents, originality, question_count, downloads, created_at
       FROM packs WHERE author_id = ? AND status = 'approved' ORDER BY created_at DESC LIMIT 50`,
  )
    .bind(row.id)
    .all();
  const posts = await c.env.DB.prepare(
    `SELECT id, title, like_count, comment_count, created_at
       FROM posts WHERE author_id = ? AND status = 'visible' ORDER BY created_at DESC LIMIT 50`,
  )
    .bind(row.id)
    .all();
  return c.json({ user: row, packs: packs.results, posts: posts.results });
});

authRoutes.get("/desktop-tokens", async (c) => {
  const user = requireUser(c);
  const rows = await c.env.DB.prepare(
    `SELECT id, label, created_at, last_used_at
       FROM desktop_tokens
      WHERE user_id = ? AND revoked_at IS NULL
      ORDER BY created_at DESC LIMIT 20`,
  )
    .bind(user.id)
    .all();
  return c.json({ tokens: rows.results ?? [] });
});

authRoutes.post("/desktop-tokens", async (c) => {
  const user = requireUser(c);
  const body = await readJson(c).catch(() => ({}));
  const label = text((body as Record<string, unknown>).label, 40) || "题有据桌面端";
  const token = `tyj_dt_${bytesToBase64Url(randomBytes(32))}`;
  const inserted = await c.env.DB.prepare(
    "INSERT INTO desktop_tokens (token_hash, user_id, label) VALUES (?, ?, ?) RETURNING id, created_at",
  )
    .bind(await sha256Hex(token), user.id, label)
    .first<{ id: number; created_at: string }>();
  return c.json(
    {
      id: inserted?.id,
      token,
      label,
      created_at: inserted?.created_at,
      user: publicUser(user),
    },
    201,
  );
});

authRoutes.delete("/desktop-tokens/:id", async (c) => {
  const user = requireUser(c);
  const id = Number(c.req.param("id"));
  if (!Number.isInteger(id) || id <= 0) fail(400, "令牌编号不对。");
  const res = await c.env.DB.prepare("UPDATE desktop_tokens SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL")
    .bind(nowIso(), id, user.id)
    .run();
  if (!res.meta?.changes) fail(404, "没有找到这个授权。");
  return c.json({ ok: true });
});
