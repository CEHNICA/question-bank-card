import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { Client, makeEnv, promoteAdmin, stubTurnstile } from "./helpers.ts";
import { hashPassword, verifyPassword } from "../worker/src/auth.ts";

let restore: () => void;
before(() => {
  restore = stubTurnstile();
});
after(() => restore());

test("config exposes only public settings", async () => {
  const env = makeEnv();
  const { status, data } = await new Client(env).json("GET", "/api/config");
  assert.equal(status, 200);
  assert.equal(data.turnstile_site_key, "1x00000000000000000000AA");
  assert.equal(JSON.stringify(data).includes("test-secret"), false);
  assert.equal(JSON.stringify(data).includes("download"), false);
});

test("passwords are salted PBKDF2 hashes and verify correctly", async () => {
  const first = await hashPassword("password-123");
  const second = await hashPassword("password-123");
  assert.notEqual(first, second);
  assert.match(first, /^pbkdf2\$100000\$/);
  assert.equal(await verifyPassword("password-123", first), true);
  assert.equal(await verifyPassword("wrong-password", first), false);
});

test("register, me, logout, login round trip with HttpOnly cookie", async () => {
  const env = makeEnv();
  const client = new Client(env);
  const reg = await client.register("虚构老师");
  assert.equal(reg.status, 201);
  assert.ok(client.cookie.startsWith("tyj_session="));
  const stored = env.DB.db.prepare("SELECT password_hash FROM users").get() as { password_hash: string };
  assert.equal(stored.password_hash.includes("password-123"), false);
  const session = env.DB.db.prepare("SELECT token_hash FROM sessions").get() as { token_hash: string };
  assert.equal(client.cookie.includes(session.token_hash), false, "库里只存会话哈希");

  assert.equal((await client.json("GET", "/api/me")).data.user.username, "虚构老师");
  await client.json("POST", "/api/logout", {});
  assert.equal((await client.json("GET", "/api/me")).data.user, null);

  const bad = await client.json("POST", "/api/login", { username: "虚构老师", password: "nope-nope", turnstile_token: "ok" });
  assert.equal(bad.status, 401);
  const good = await client.json("POST", "/api/login", {
    username: "虚构老师",
    password: "password-123",
    turnstile_token: "ok",
  });
  assert.equal(good.status, 200);
  assert.equal((await client.json("GET", "/api/me")).data.user.username, "虚构老师");
});

test("register requires Turnstile, terms and unique names", async () => {
  const env = makeEnv();
  const client = new Client(env);
  const noCaptcha = await client.json("POST", "/api/register", {
    username: "abc",
    password: "password-123",
    turnstile_token: "bad",
    agree_terms: true,
  });
  assert.equal(noCaptcha.status, 400);
  const noTerms = await client.json("POST", "/api/register", {
    username: "abc",
    password: "password-123",
    turnstile_token: "ok",
  });
  assert.equal(noTerms.status, 400);
  assert.equal((await client.register("abc")).status, 201);
  assert.equal((await new Client(env).register("ABC")).status, 409, "用户名不分大小写");
});

test("missing Turnstile secret refuses instead of silently skipping", async () => {
  const env = { ...makeEnv(), TURNSTILE_SECRET: "" };
  const { status } = await new Client(env).register("abc");
  assert.equal(status, 503);
});

test("writes without the same-origin header are rejected", async () => {
  const env = makeEnv();
  const client = new Client(env);
  const response = await client.app.request(
    "http://community.test/api/register",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" },
    env,
  );
  assert.equal(response.status, 403);
  const cross = await client.request("POST", "/api/logout", {}, { Origin: "https://evil.example" });
  assert.equal(cross.status, 403);
});

test("login failures are rate limited", async () => {
  const env = makeEnv();
  const client = new Client(env);
  await client.register("abc");
  for (let i = 0; i < 8; i++) {
    await client.json("POST", "/api/login", { username: "abc", password: "wrong-pass", turnstile_token: "ok" });
  }
  const locked = await client.json("POST", "/api/login", { username: "abc", password: "password-123", turnstile_token: "ok" });
  assert.equal(locked.status, 429);
});

test("admin routes need the admin role and bans end sessions", async () => {
  const env = makeEnv();
  const admin = new Client(env);
  await admin.register("admin1");
  const user = new Client(env);
  const reg = await user.register("user1");
  assert.equal((await user.json("GET", "/api/admin/packs")).status, 403);
  assert.equal((await admin.json("GET", "/api/admin/packs")).status, 403);
  promoteAdmin(env, "admin1");
  assert.equal((await admin.json("GET", "/api/admin/packs")).status, 200);

  const ban = await admin.json("POST", `/api/admin/users/${reg.data.user.id}/ban`, { banned: true });
  assert.equal(ban.status, 200);
  assert.equal((await user.json("GET", "/api/me")).data.user, null);
  const relogin = await user.json("POST", "/api/login", { username: "user1", password: "password-123", turnstile_token: "ok" });
  assert.equal(relogin.status, 403);
});
