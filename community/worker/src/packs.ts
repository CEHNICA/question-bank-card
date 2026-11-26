import { Hono } from "hono";
import { unzipSync } from "fflate";
import type { AppBindings, Env, SessionUser } from "./env.ts";
import {
  type AppContext,
  base64UrlToBytes,
  bytesToBase64Url,
  fail,
  hmacSign,
  intParam,
  nowIso,
  readJson,
  requireUser,
  text,
  timingSafeEqual,
} from "./util.ts";
import { assertCanSell, authorPayment, parsePriceYuan } from "./sales.ts";

export const MANIFEST_NAME = "题有据备份.json";
export const DATABASE_NAME = "题库.sqlite3";
export const PREVIEW_NAME = "community-preview.json";
const COVER_PATTERN = /^community-cover\.(png|jpe?g|webp)$/;
const COVER_MAX_BYTES = 1024 * 1024;
const DOWNLOAD_TTL_MS = 5 * 60 * 1000;
export const STAGES = ["小学", "初中", "高中", "大学", "其他"];

type PackRow = {
  id: string;
  author_id: number;
  title: string;
  status: string;
  price_cents: number;
  originality: string;
  include_originals: number;
  r2_key: string;
  cover_key: string | null;
};

export type CleanPreview = {
  counts: { papers: number; questions: number; tags: number };
  question_types: Record<string, number>;
  tags: { name: string; questions: number }[];
  samples: { number: unknown; question_type: string; stem: string; options: { key: string; text: string }[]; figures: number }[];
  papers: { title: string; questions: number }[];
  include_originals: boolean;
};

type ParsedPack = {
  preview: CleanPreview;
  cover: { bytes: Uint8Array; contentType: string; ext: string } | null;
};

function num(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : 0;
}

/** 只留展示需要的字段，并限长 —— 预览来自用户电脑，不信任里面的任何多余内容。 */
export function cleanPreview(raw: unknown): CleanPreview {
  const source = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const counts = (source.counts && typeof source.counts === "object" ? source.counts : {}) as Record<string, unknown>;
  const types: Record<string, number> = {};
  const rawTypes = source.question_types;
  if (rawTypes && typeof rawTypes === "object") {
    for (const [key, value] of Object.entries(rawTypes).slice(0, 20)) types[String(key).slice(0, 40)] = num(value);
  }
  const tags = (Array.isArray(source.tags) ? source.tags : []).slice(0, 30).map((tag) => {
    const item = (tag && typeof tag === "object" ? tag : {}) as Record<string, unknown>;
    return { name: text(item.name, 60), questions: num(item.questions) };
  });
  const samples = (Array.isArray(source.samples) ? source.samples : []).slice(0, 3).map((sample) => {
    const item = (sample && typeof sample === "object" ? sample : {}) as Record<string, unknown>;
    const options = (Array.isArray(item.options) ? item.options : []).slice(0, 8).map((option) => {
      const row = (option && typeof option === "object" ? option : {}) as Record<string, unknown>;
      return { key: text(row.key, 4), text: text(row.text, 1000) };
    });
    const number = typeof item.number === "number" || typeof item.number === "string" ? item.number : "";
    return {
      number,
      question_type: text(item.question_type, 40),
      stem: text(item.stem, 4000),
      options,
      figures: num(item.figures),
    };
  });
  const papers = (Array.isArray(source.papers) ? source.papers : []).slice(0, 20).map((paper) => {
    const item = (paper && typeof paper === "object" ? paper : {}) as Record<string, unknown>;
    return { title: text(item.title, 120), questions: num(item.questions) };
  });
  return {
    counts: { papers: num(counts.papers), questions: num(counts.questions), tags: num(counts.tags) },
    question_types: types,
    tags,
    samples,
    papers,
    include_originals: Boolean(source.include_originals),
  };
}

/** 认出这是题有据桌面版「导出社区题包」打出来的 zip，并取出预览和封面。 */
export function parsePackZip(bytes: Uint8Array): ParsedPack {
  const seen = new Set<string>();
  let files: Record<string, Uint8Array>;
  try {
    files = unzipSync(bytes, {
      filter: (file) => {
        seen.add(file.name);
        if (file.name === MANIFEST_NAME || file.name === PREVIEW_NAME) return file.originalSize < 4 * 1024 * 1024;
        return COVER_PATTERN.test(file.name) && file.originalSize <= COVER_MAX_BYTES;
      },
    });
  } catch {
    fail(400, "这个文件打不开，不是完整的 zip 题包。");
  }
  if (!seen.has(DATABASE_NAME) || !files[MANIFEST_NAME]) {
    fail(400, "这不是题有据导出的题包：缺少清单或题库。");
  }
  const decoder = new TextDecoder();
  let manifest: Record<string, unknown>;
  try {
    manifest = JSON.parse(decoder.decode(files[MANIFEST_NAME]));
  } catch {
    fail(400, "题包清单读不出来。");
  }
  if (manifest.format !== 1) fail(400, "题包格式版本不认识，请升级题有据后重新导出。");
  if (!manifest.community || !files[PREVIEW_NAME]) {
    fail(400, "请用题有据桌面版的「导出为社区题包」重新导出，普通备份包不能直接发到社区。");
  }
  let rawPreview: unknown;
  try {
    rawPreview = JSON.parse(decoder.decode(files[PREVIEW_NAME]));
  } catch {
    fail(400, "题包预览读不出来，请重新导出。");
  }
  const preview = cleanPreview(rawPreview);
  const community = manifest.community as Record<string, unknown>;
  preview.include_originals = Boolean(community.include_originals);
  let cover: ParsedPack["cover"] = null;
  for (const [name, data] of Object.entries(files)) {
    const match = COVER_PATTERN.exec(name);
    if (match) {
      const ext = match[1] === "jpeg" ? "jpg" : match[1];
      const contentType = ext === "jpg" ? "image/jpeg" : `image/${ext}`;
      cover = { bytes: data, contentType, ext };
      break;
    }
  }
  return { preview, cover };
}

export async function loadPack(env: Env, id: string): Promise<PackRow | null> {
  return (
    (await env.DB.prepare(
      `SELECT id, author_id, title, status, price_cents, originality, include_originals, r2_key, cover_key
         FROM packs WHERE id = ?`,
    )
      .bind(id)
      .first<PackRow>()) ?? null
  );
}

export function canManage(user: SessionUser | null, pack: PackRow): boolean {
  return Boolean(user && !user.banned_at && (user.role === "admin" || user.id === pack.author_id));
}

export async function hasEntitlement(env: Env, userId: number, packId: string): Promise<boolean> {
  const row = await env.DB.prepare("SELECT 1 AS ok FROM entitlements WHERE user_id = ? AND pack_id = ?")
    .bind(userId, packId)
    .first();
  return Boolean(row);
}

async function downloadSecret(env: Env): Promise<string> {
  if (!env.DOWNLOAD_SECRET || env.DOWNLOAD_SECRET.length < 16) {
    fail(503, "网站还没配置下载签名密钥，暂时不能下载。");
  }
  return env.DOWNLOAD_SECRET;
}

export async function signDownload(env: Env, packId: string, userId: number, now = Date.now()): Promise<string> {
  const payload = bytesToBase64Url(new TextEncoder().encode(`${packId}.${userId}.${now + DOWNLOAD_TTL_MS}`));
  return `${payload}.${await hmacSign(await downloadSecret(env), payload)}`;
}

export async function verifyDownload(env: Env, token: string): Promise<{ packId: string; userId: number }> {
  const [payload, signature] = token.split(".");
  if (!payload || !signature) fail(403, "下载链接无效。");
  const expected = await hmacSign(await downloadSecret(env), payload);
  if (!timingSafeEqual(signature, expected)) fail(403, "下载链接无效。");
  const [packId, rawUser, rawExp] = new TextDecoder().decode(base64UrlToBytes(payload)).split(".");
  if (!packId || Number(rawExp) < Date.now()) fail(403, "下载链接过期了，请回到题包页面重新点下载。");
  return { packId, userId: Number(rawUser) };
}

export const packRoutes = new Hono<AppBindings>();

packRoutes.get("/packs", async (c) => {
  const q = text(c.req.query("q"), 60);
  const stage = text(c.req.query("stage"), 10);
  const subject = text(c.req.query("subject"), 20);
  const year = intParam(c.req.query("year"), 0, 0, 3000);
  const tag = text(c.req.query("tag"), 60);
  const price = c.req.query("price");
  const sort = c.req.query("sort") === "popular" ? "p.downloads DESC, p.created_at DESC" : "p.created_at DESC";
  const page = intParam(c.req.query("page"), 1, 1, 1000);
  const pageSize = 24;
  const where = ["p.status = 'approved'"];
  const params: unknown[] = [];
  if (q) {
    where.push("(p.title LIKE ? OR p.description LIKE ? OR p.tags_text LIKE ?)");
    params.push(`%${q}%`, `%${q}%`, `%${q}%`);
  }
  if (stage) {
    where.push("p.stage = ?");
    params.push(stage);
  }
  if (subject) {
    where.push("p.subject = ?");
    params.push(subject);
  }
  if (year) {
    where.push("p.year = ?");
    params.push(year);
  }
  if (tag) {
    where.push("(',' || p.tags_text || ',') LIKE ?");
    params.push(`%,${tag},%`);
  }
  if (price === "free") where.push("p.price_cents = 0");
  if (price === "paid") where.push("p.price_cents > 0");
  const clause = where.join(" AND ");
  const rows = await c.env.DB.prepare(
    `SELECT p.id, p.title, p.stage, p.subject, p.year, p.tags_text, p.price_cents, p.originality, p.question_count,
            p.paper_count, p.downloads, p.created_at, p.cover_key IS NOT NULL AS has_cover,
            u.username AS author_username, u.display_name AS author_name
       FROM packs p JOIN users u ON u.id = p.author_id
      WHERE ${clause}
      ORDER BY ${sort} LIMIT ? OFFSET ?`,
  )
    .bind(...params, pageSize + 1, (page - 1) * pageSize)
    .all();
  const results = rows.results ?? [];
  const facets = await c.env.DB.prepare(
    `SELECT stage, subject, year, COUNT(*) AS n FROM packs WHERE status = 'approved'
      GROUP BY stage, subject, year`,
  ).all();
  return c.json({
    packs: results.slice(0, pageSize),
    has_more: results.length > pageSize,
    page,
    facets: facets.results ?? [],
  });
});

packRoutes.post("/packs", async (c) => {
  const user = requireUser(c);
  const maxBytes = Number(c.env.MAX_PACK_MB || 50) * 1024 * 1024;
  const declared = Number(c.req.header("Content-Length") || 0);
  if (declared > maxBytes + 64 * 1024) fail(413, `题包太大了，单个不能超过 ${c.env.MAX_PACK_MB || 50} MB。`);
  const form = await c.req.parseBody();
  const file = form.file;
  if (!(file instanceof File)) fail(400, "请选择一个题包 zip 文件。");
  if (file.size > maxBytes) fail(413, `题包太大了，单个不能超过 ${c.env.MAX_PACK_MB || 50} MB。`);
  if (form.rights_confirmed !== "1") fail(400, "请先确认你有权分发这份题包。");
  const title = text(form.title, 80);
  if (title.length < 2) fail(400, "请填一个标题。");
  const stage = text(form.stage, 10);
  if (stage && !STAGES.includes(stage)) fail(400, "学段只能选小学、初中、高中、大学或其他。");
  const subject = text(form.subject, 20) || "数学";
  const year = intParam(form.year, 0, 0, 3000) || null;
  const tags = text(form.tags, 300)
    .split(/[,，、\s]+/)
    .map((tag) => tag.trim())
    .filter(Boolean)
    .slice(0, 12);
  const price = parsePriceYuan(form.price_yuan);
  const originality = form.originality === "original" ? "original" : "compiled";
  const description = text(form.description, 5000);

  const bytes = new Uint8Array(await file.arrayBuffer());
  const parsed = parsePackZip(bytes);
  if (parsed.preview.include_originals && form.originals_confirmed !== "1") {
    fail(400, "这份题包带了原卷文件，请再确认原卷版权归你或已获授权。");
  }
  if (price > 0) {
    await assertCanSell(c.env, {
      author_id: user.id,
      originality,
      include_originals: parsed.preview.include_originals,
    });
  }
  const id = crypto.randomUUID();
  const r2Key = `packs/${id}.zip`;
  await c.env.PACKS.put(r2Key, bytes, {
    httpMetadata: { contentType: "application/zip" },
    customMetadata: { author: String(user.id) },
  });
  let coverKey: string | null = null;
  if (parsed.cover) {
    coverKey = `covers/${id}.${parsed.cover.ext}`;
    await c.env.PACKS.put(coverKey, parsed.cover.bytes, { httpMetadata: { contentType: parsed.cover.contentType } });
  }
  const tagNames = tags.length ? tags : parsed.preview.tags.slice(0, 8).map((tag) => tag.name);
  try {
    await c.env.DB.batch([
      c.env.DB.prepare(
        `INSERT INTO packs (id, author_id, title, description, stage, subject, year, tags_text, price_cents,
                            originality, r2_key, cover_key, size, question_count, paper_count, include_originals,
                            rights_confirmed_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        id,
        user.id,
        title,
        description,
        stage,
        subject,
        year,
        tagNames.join(","),
        price,
        originality,
        r2Key,
        coverKey,
        bytes.byteLength,
        parsed.preview.counts.questions,
        parsed.preview.counts.papers,
        parsed.preview.include_originals ? 1 : 0,
        nowIso(),
      ),
      c.env.DB.prepare("INSERT INTO pack_previews (pack_id, preview_json) VALUES (?, ?)").bind(
        id,
        JSON.stringify(parsed.preview),
      ),
    ]);
  } catch (error) {
    await c.env.PACKS.delete([r2Key, ...(coverKey ? [coverKey] : [])]);
    throw error;
  }
  return c.json({ id, status: "pending" }, 201);
});

packRoutes.get("/packs/:id", async (c) => {
  const user = c.get("user");
  const row = await c.env.DB.prepare(
    `SELECT p.*, u.username AS author_username, u.display_name AS author_name, v.preview_json
       FROM packs p JOIN users u ON u.id = p.author_id
       LEFT JOIN pack_previews v ON v.pack_id = p.id
      WHERE p.id = ?`,
  )
    .bind(c.req.param("id"))
    .first<Record<string, unknown> & PackRow>();
  if (!row) fail(404, "没有这份题包。");
  const manage = canManage(user, row);
  if (row.status !== "approved" && !manage) fail(404, "没有这份题包。");
  const owned = user ? manage || (await hasEntitlement(c.env, user.id, row.id)) : false;
  const posts = await c.env.DB.prepare(
    `SELECT p.id, p.title, p.like_count, p.comment_count, p.created_at, u.username AS author_username,
            u.display_name AS author_name
       FROM posts p JOIN users u ON u.id = p.author_id
      WHERE p.pack_id = ? AND p.status = 'visible' ORDER BY p.created_at DESC LIMIT 20`,
  )
    .bind(row.id)
    .all();
  const paid = row.price_cents > 0;
  let payment = null;
  let myRequest = null;
  if (user && paid && row.status === "approved" && !owned) {
    payment = await authorPayment(c.env, row.author_id);
    myRequest =
      (await c.env.DB.prepare(
        `SELECT id, status, buyer_note, decline_reason, created_at, decided_at FROM purchase_requests
          WHERE buyer_id = ? AND pack_id = ? AND status IN ('pending', 'declined')
          ORDER BY created_at DESC LIMIT 1`,
      )
        .bind(user.id, row.id)
        .first()) ?? null;
  }
  const { preview_json: previewJson, r2_key: _r2, rights_confirmed_at: _rights, ...rest } = row;
  return c.json({
    pack: {
      ...rest,
      cover_key: undefined,
      has_cover: Boolean(row.cover_key),
      review_note: manage ? row.review_note : undefined,
      reviewed_by: undefined,
    },
    preview: previewJson ? JSON.parse(String(previewJson)) : null,
    viewer: { can_manage: manage, owned, can_download: row.status === "approved" && (!paid || owned) },
    payment,
    my_request: myRequest,
    posts: posts.results ?? [],
  });
});

packRoutes.get("/packs/:id/cover", async (c) => {
  const pack = await loadPack(c.env, c.req.param("id"));
  if (!pack || !pack.cover_key || (pack.status !== "approved" && !canManage(c.get("user"), pack))) {
    fail(404, "没有封面。");
  }
  const object = await c.env.PACKS.get(pack.cover_key);
  if (!object) fail(404, "没有封面。");
  return new Response(object.body as unknown as ReadableStream, {
    headers: {
      "Content-Type": object.httpMetadata?.contentType || "image/png",
      "Cache-Control": pack.status === "approved" ? "public, max-age=3600" : "private, no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
});

packRoutes.post("/packs/:id/download", async (c) => {
  const user = requireUser(c);
  const pack = await loadPack(c.env, c.req.param("id"));
  if (!pack) fail(404, "没有这份题包。");
  const manage = canManage(user, pack);
  if (pack.status !== "approved" && !manage) fail(404, "没有这份题包。");
  if (pack.price_cents > 0 && !manage && !(await hasEntitlement(c.env, user.id, pack.id))) {
    fail(402, "这是付费题包，请先向作者付款，等作者开通或用兑换码兑换后再下载。");
  }
  const token = await signDownload(c.env, pack.id, user.id);
  return c.json({ url: `/api/dl/${token}`, expires_in: DOWNLOAD_TTL_MS / 1000 });
});

packRoutes.get("/dl/:token", async (c) => {
  const { packId } = await verifyDownload(c.env, c.req.param("token"));
  const pack = await loadPack(c.env, packId);
  if (!pack || pack.status === "taken_down") fail(404, "这份题包已经下架了。");
  const object = await c.env.PACKS.get(pack.r2_key);
  if (!object) fail(404, "题包文件找不到了，请联系作者。");
  await c.env.DB.prepare("UPDATE packs SET downloads = downloads + 1 WHERE id = ?").bind(pack.id).run();
  const safeName = `${pack.title.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "_").slice(0, 60) || "题包"}.zip`;
  return new Response(object.body as unknown as ReadableStream, {
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": `attachment; filename="tiyouju-pack.zip"; filename*=UTF-8''${encodeURIComponent(safeName)}`,
      "Cache-Control": "private, no-store",
    },
  });
});

packRoutes.patch("/packs/:id", async (c) => {
  const user = requireUser(c);
  const pack = await loadPack(c.env, c.req.param("id"));
  if (!pack || !canManage(user, pack)) fail(404, "没有这份题包。");
  const body = await readJson(c);
  const title = text(body.title, 80);
  if (title.length < 2) fail(400, "请填一个标题。");
  const description = text(body.description, 5000);
  const price = parsePriceYuan(body.price_yuan, pack.price_cents);
  if (price > 0) await assertCanSell(c.env, pack);
  await c.env.DB.prepare(
    "UPDATE packs SET title = ?, description = ?, price_cents = ?, updated_at = ? WHERE id = ?",
  )
    .bind(title, description, price, nowIso(), pack.id)
    .run();
  return c.json({ ok: true });
});

packRoutes.post("/packs/:id/withdraw", async (c) => {
  const user = requireUser(c);
  const pack = await loadPack(c.env, c.req.param("id"));
  if (!pack || pack.author_id !== user.id) fail(404, "没有这份题包。");
  if (pack.status === "taken_down") fail(400, "已经被管理员下架了。");
  await c.env.DB.prepare("UPDATE packs SET status = 'withdrawn', updated_at = ? WHERE id = ?")
    .bind(nowIso(), pack.id)
    .run();
  return c.json({ ok: true });
});

packRoutes.get("/me/packs", async (c) => {
  const user = requireUser(c);
  const mine = await c.env.DB.prepare(
    `SELECT p.id, p.title, p.status, p.review_note, p.price_cents, p.originality, p.question_count, p.downloads,
            p.created_at,
            (SELECT COUNT(*) FROM entitlements e WHERE e.pack_id = p.id) AS buyers,
            (SELECT COALESCE(SUM(e.price_cents), 0) FROM entitlements e WHERE e.pack_id = p.id) AS estimated_cents,
            (SELECT COUNT(*) FROM purchase_requests r WHERE r.pack_id = p.id AND r.status = 'pending') AS pending_requests
       FROM packs p WHERE p.author_id = ? ORDER BY p.created_at DESC`,
  )
    .bind(user.id)
    .all();
  const owned = await c.env.DB.prepare(
    `SELECT p.id, p.title, p.status, p.price_cents, p.question_count, e.created_at AS redeemed_at, e.source,
            u.display_name AS author_name
       FROM entitlements e JOIN packs p ON p.id = e.pack_id JOIN users u ON u.id = p.author_id
      WHERE e.user_id = ? ORDER BY e.created_at DESC`,
  )
    .bind(user.id)
    .all();
  return c.json({ packs: mine.results ?? [], owned: owned.results ?? [] });
});

export async function fileReport(c: AppContext, targetType: "pack" | "post" | "comment", targetId: string) {
  const user = requireUser(c);
  const body = await readJson(c);
  const reason = text(body.reason, 1000);
  if (reason.length < 4) fail(400, "请写清楚举报理由（至少 4 个字）。");
  const duplicate = await c.env.DB.prepare(
    "SELECT id FROM reports WHERE reporter_id = ? AND target_type = ? AND target_id = ? AND status = 'open'",
  )
    .bind(user.id, targetType, targetId)
    .first();
  if (duplicate) return c.json({ ok: true, duplicate: true });
  await c.env.DB.prepare("INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (?, ?, ?, ?)")
    .bind(user.id, targetType, targetId, reason)
    .run();
  return c.json({ ok: true }, 201);
}

packRoutes.post("/packs/:id/report", async (c) => {
  const pack = await loadPack(c.env, c.req.param("id"));
  if (!pack) fail(404, "没有这份题包。");
  return fileReport(c, "pack", pack.id);
});
