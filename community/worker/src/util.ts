import type { Context } from "hono";
import type { AppBindings, Env } from "./env.ts";

export type AppContext = Context<AppBindings>;

export class HttpError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export function fail(status: number, message: string): never {
  throw new HttpError(status, message);
}

export function nowIso(): string {
  return new Date().toISOString();
}

const encoder = new TextEncoder();

export function bytesToBase64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function base64UrlToBytes(text: string): Uint8Array {
  const padded = text.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((text.length + 3) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

export function randomBytes(length: number): Uint8Array {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return bytes;
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function hmacSign(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey("raw", encoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
  ]);
  const signature = await crypto.subtle.sign("HMAC", key, encoder.encode(message));
  return bytesToBase64Url(new Uint8Array(signature));
}

export function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function readJson(c: AppContext): Promise<Record<string, unknown>> {
  try {
    const body = await c.req.json();
    if (body && typeof body === "object" && !Array.isArray(body)) return body as Record<string, unknown>;
  } catch {
    // 落到下面统一报错
  }
  fail(400, "请求格式不正确。");
}

export function text(value: unknown, max: number): string {
  return String(value ?? "")
    .replace(/\r\n/g, "\n")
    .trim()
    .slice(0, max);
}

export function intParam(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

export function clientIp(c: AppContext): string {
  return c.req.header("CF-Connecting-IP") || c.req.header("X-Forwarded-For")?.split(",")[0]?.trim() || "local";
}

/** 窗口内超过 limit 次就拒绝；只在调用 hit() 时记一笔，查询不记。 */
export async function rateLimited(env: Env, key: string, limit: number, windowMs: number): Promise<boolean> {
  const since = Date.now() - windowMs;
  const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM rate_events WHERE key = ? AND at > ?")
    .bind(key, since)
    .first<{ n: number }>();
  return Number(row?.n ?? 0) >= limit;
}

export async function hit(env: Env, key: string): Promise<void> {
  const now = Date.now();
  await env.DB.batch([
    env.DB.prepare("INSERT INTO rate_events (key, at) VALUES (?, ?)").bind(key, now),
    env.DB.prepare("DELETE FROM rate_events WHERE at < ?").bind(now - 24 * 3600 * 1000),
  ]);
}

export function requireUser(c: AppContext) {
  const user = c.get("user");
  if (!user) fail(401, "请先登录。");
  if (user.banned_at) fail(403, "这个账号已被停用。");
  return user;
}

export function requireAdmin(c: AppContext) {
  const user = requireUser(c);
  if (user.role !== "admin") fail(403, "只有管理员能做这件事。");
  return user;
}
