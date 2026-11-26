// 测试替身：用 node:sqlite 模拟 D1，用内存 Map 模拟 R2。只实现 Worker 实际用到的那几个方法。
import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { strToU8, zipSync } from "fflate";
import { createApp } from "../worker/src/index.ts";

type Value = string | number | null | bigint | Uint8Array;

class FakeStatement {
  db: DatabaseSync;
  sql: string;
  params: Value[] = [];
  constructor(db: DatabaseSync, sql: string) {
    this.db = db;
    this.sql = sql;
  }
  bind(...params: unknown[]) {
    const next = new FakeStatement(this.db, this.sql);
    next.params = params.map((value) => {
      if (value === undefined) throw new Error(`undefined bound in: ${this.sql}`);
      if (typeof value === "boolean") return value ? 1 : 0;
      return value as Value;
    });
    return next;
  }
  async first<T>(column?: string): Promise<T | null> {
    const row = this.db.prepare(this.sql).get(...this.params) as Record<string, unknown> | undefined;
    if (!row) return null;
    return (column ? row[column] : { ...row }) as T;
  }
  async all<T>() {
    const rows = this.db.prepare(this.sql).all(...this.params) as T[];
    return { results: rows.map((row) => ({ ...row })), success: true, meta: {} };
  }
  runSync() {
    const info = this.db.prepare(this.sql).run(...this.params);
    return { success: true, meta: { changes: Number(info.changes), last_row_id: Number(info.lastInsertRowid) } };
  }
  async run() {
    return this.runSync();
  }
}

export class FakeD1 {
  db: DatabaseSync;
  constructor() {
    this.db = new DatabaseSync(":memory:");
    this.db.exec("PRAGMA foreign_keys = ON");
    const schema = readFileSync(fileURLToPath(new URL("../schema.sql", import.meta.url)), "utf8");
    this.db.exec(schema);
  }
  prepare(sql: string) {
    return new FakeStatement(this.db, sql);
  }
  async batch(statements: FakeStatement[]) {
    this.db.exec("BEGIN");
    try {
      const results = statements.map((statement) => statement.runSync());
      this.db.exec("COMMIT");
      return results;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }
}

export class FakeR2 {
  objects = new Map<string, { bytes: Uint8Array; contentType?: string }>();
  async put(
    key: string,
    value: Uint8Array | ArrayBuffer | ReadableStream<Uint8Array>,
    options?: { httpMetadata?: { contentType?: string } },
  ) {
    let bytes: Uint8Array;
    if (value instanceof Uint8Array) {
      bytes = value.slice();
    } else if (value instanceof ArrayBuffer) {
      bytes = new Uint8Array(value);
    } else {
      const reader = value.getReader();
      const chunks: Uint8Array[] = [];
      let total = 0;
      while (true) {
        const { done, value: chunk } = await reader.read();
        if (done) break;
        if (chunk) {
          chunks.push(chunk);
          total += chunk.byteLength;
        }
      }
      bytes = new Uint8Array(total);
      let offset = 0;
      for (const chunk of chunks) {
        bytes.set(chunk, offset);
        offset += chunk.byteLength;
      }
    }
    this.objects.set(key, { bytes, contentType: options?.httpMetadata?.contentType });
  }
  async get(key: string) {
    const object = this.objects.get(key);
    if (!object) return null;
    return {
      body: new Blob([object.bytes]).stream(),
      size: object.bytes.byteLength,
      httpMetadata: { contentType: object.contentType },
    };
  }
  async delete(keys: string | string[]) {
    for (const key of Array.isArray(keys) ? keys : [keys]) this.objects.delete(key);
  }
}

export function makeEnv(overrides: Partial<{ MAX_PACK_MB: string; MAX_TOTAL_R2_MB: string; MAX_DAILY_UPLOADS_PER_USER: string }> = {}) {
  return {
    DB: new FakeD1(),
    PACKS: new FakeR2(),
    TURNSTILE_SITE_KEY: "1x00000000000000000000AA",
    TURNSTILE_SECRET: "test-secret",
    DOWNLOAD_SECRET: "test-download-secret-0123456789",
    MAX_PACK_MB: "5",
    MAX_TOTAL_R2_MB: "8500",
    MAX_DAILY_UPLOADS_PER_USER: "10",
    ...overrides,
  };
}

const realFetch = globalThis.fetch;
export function stubTurnstile() {
  globalThis.fetch = (async (input: unknown, init?: RequestInit) => {
    const url = String(input instanceof Request ? input.url : input);
    if (url.includes("turnstile/v0/siteverify")) {
      const form = init?.body as FormData;
      return Response.json({ success: form.get("response") === "ok" });
    }
    return realFetch(input as RequestInfo, init);
  }) as typeof fetch;
  return () => {
    globalThis.fetch = realFetch;
  };
}

export type Env = ReturnType<typeof makeEnv>;

/** 每个 Client 一个 cookie 罐，模拟一个浏览器。 */
export class Client {
  app = createApp();
  env: Env;
  cookie = "";
  constructor(env: Env) {
    this.env = env;
  }
  async request(method: string, path: string, body?: unknown, extraHeaders: Record<string, string> = {}) {
    const headers: Record<string, string> = { "X-TYJ": "1", ...extraHeaders };
    if (this.cookie) headers.Cookie = this.cookie;
    let payload: BodyInit | undefined;
    if (body instanceof FormData) payload = body;
    else if (body instanceof Uint8Array || body instanceof Blob) {
      headers["Content-Type"] = extraHeaders["Content-Type"] || "application/zip";
      payload = body as BodyInit;
    } else if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const response = await this.app.request(`http://community.test${path}`, { method, headers, body: payload }, this.env);
    const setCookie = response.headers.get("Set-Cookie");
    if (setCookie) {
      const pair = setCookie.split(";")[0];
      this.cookie = pair.endsWith("=") ? "" : pair;
    }
    return response;
  }
  async json(method: string, path: string, body?: unknown) {
    const response = await this.request(method, path, body);
    const data = (await response.json()) as Record<string, any>;
    return { status: response.status, data };
  }
  async register(username: string, password = "password-123") {
    return this.json("POST", "/api/register", { username, password, turnstile_token: "ok", agree_terms: true });
  }
}

export function promoteAdmin(env: Env, username: string) {
  env.DB.db.prepare("UPDATE users SET role = 'admin' WHERE username = ?").run(username);
}

export async function setPayment(client: Client, note = "（虚构）微信扫码，备注用户名") {
  const res = await client.json("POST", "/api/me/payment", { note });
  if (res.status !== 200) throw new Error(`set payment failed: ${JSON.stringify(res.data)}`);
}

export type PackOptions = {
  community?: boolean;
  includeOriginals?: boolean;
  preview?: Record<string, unknown>;
  cover?: boolean;
};

/** 造一个和桌面版「导出为社区题包」同结构的虚构 zip。 */
export function makePackZip(options: PackOptions = {}) {
  const manifest: Record<string, unknown> = {
    format: 1,
    app_version: "2.3.5",
    created_at: "2026-11-26T00:00:00Z",
    counts: { papers: 1, questions: 2 },
    papers: [{ id: "p1", title: "（虚构）期中卷", files: {} }],
  };
  if (options.community !== false) manifest.community = { format: 1, include_originals: Boolean(options.includeOriginals) };
  const preview = options.preview ?? {
    format: 1,
    counts: { papers: 1, questions: 2, tags: 1 },
    question_types: { single_choice: 2 },
    tags: [{ name: "（虚构）函数", kind: "knowledge", questions: 2 }],
    samples: [
      {
        number: 1,
        question_type: "single_choice",
        stem: "（虚构）已知 $f(x)=x^2$，求 $f(2)$。",
        options: [
          { key: "A", text: "$2$" },
          { key: "B", text: "$4$" },
        ],
        figures: 0,
        answer: "（虚构）不该出现的答案",
      },
    ],
    papers: [{ title: "（虚构）期中卷", questions: 2 }],
    secret_field: "（虚构）多余字段",
  };
  const files: Record<string, Uint8Array> = {
    "题有据备份.json": strToU8(JSON.stringify(manifest)),
    "题库.sqlite3": strToU8("SQLite format 3\u0000 fake"),
    "原卷/library/x/crop.png": new Uint8Array([137, 80, 78, 71]),
  };
  if (options.community !== false) files["community-preview.json"] = strToU8(JSON.stringify(preview));
  if (options.cover) files["community-cover.png"] = new Uint8Array([137, 80, 78, 71, 13, 10, 26, 10, 1, 2, 3]);
  return zipSync(files);
}

export function packForm(zip: Uint8Array, fields: Record<string, string> = {}) {
  const form = new FormData();
  form.append("file", new File([zip], "pack.zip", { type: "application/zip" }));
  const defaults: Record<string, string> = {
    title: "（虚构）高一期中题包",
    stage: "高中",
    subject: "数学",
    year: "2026",
    tags: "函数,期中",
    price_yuan: "0",
    description: "（虚构）测试用题包",
    rights_confirmed: "1",
  };
  for (const [key, value] of Object.entries({ ...defaults, ...fields })) form.append(key, value);
  return form;
}
