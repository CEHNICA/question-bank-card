import { Hono } from "hono";
import { unzipSync } from "fflate";
import type { AppBindings, Env, SessionUser } from "./env.ts";
import {
  type AppContext,
  HttpError,
  base64UrlToBytes,
  bytesToBase64Url,
  fail,
  hit,
  hmacSign,
  intParam,
  nowIso,
  rateLimited,
  readJson,
  requireUser,
  text,
  timingSafeEqual,
} from "./util.ts";
import { assertCanSell, authorPayment, parsePriceYuan } from "./sales.ts";
import {
  WEBDAV_PREFIX,
  archiveR2ObjectToWebDav,
  deleteStoredObjects,
  getPackStream,
  hasWebDavCredentials,
  isHybridMode,
  isWebDavKey,
  isWebDavMode,
  packStorageKey,
  putPackObject,
  storageMode,
} from "./storage.ts";

export const MANIFEST_NAME = "题有据备份.json";
export const DATABASE_NAME = "题库.sqlite3";
export const PREVIEW_NAME = "community-preview.json";
const COVER_PATTERN = /^community-cover\.(png|jpe?g|webp)$/;
const COVER_MAX_BYTES = 1024 * 1024;
const ONENOTE_PREVIEW_MAX_CHARS = 180_000;
const DOWNLOAD_TTL_MS = 5 * 60 * 1000;
const DIRECT_MULTIPART_MAX_BYTES = 20 * 1024 * 1024;
const DEFAULT_MAX_TOTAL_R2_MB = 8500;
const DEFAULT_R2_HOT_TARGET_MB = 5500;
const DEFAULT_COLD_ARCHIVE_AGE_DAYS = 7;
const DEFAULT_ARCHIVE_BATCH_SIZE = 3;
const DEFAULT_USER_STORAGE_QUOTA_MB = 500;
const DEFAULT_ADMIN_MAX_PACK_MB = 95;
const DEFAULT_DAILY_UPLOADS_PER_USER = 5;
const DEFAULT_MAX_PENDING_PER_USER = 5;
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
  cold_key: string | null;
  cover_key: string | null;
  r2_purged_at: string | null;
  r2_cached: number;
  last_downloaded_at: string | null;
  archived_at: string | null;
  archive_error: string;
  size: number;
};

export type CleanPreview = {
  counts: { papers: number; questions: number; tags: number };
  question_types: Record<string, number>;
  tags: { name: string; questions: number }[];
  samples: { number: unknown; question_type: string; stem: string; options: { key: string; text: string }[]; figures: number }[];
  papers: { title: string; questions: number }[];
  include_originals: boolean;
  has_onenote_notes?: boolean;
  onenote_count?: number;
  onenote_preview?: string;
  single_question?: {
    question_images: string[];
    figures: string[];
    onenote_pages: string[];
    images_omitted: number;
    notes_locked?: boolean;
  };
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
  const cleaned: CleanPreview = {
    counts: { papers: num(counts.papers), questions: num(counts.questions), tags: num(counts.tags) },
    question_types: types,
    tags,
    samples,
    papers,
    include_originals: Boolean(source.include_originals),
  };
  if (Boolean(source.has_onenote_notes)) {
    cleaned.has_onenote_notes = true;
    const noteCount = num(source.onenote_count);
    if (noteCount > 0) cleaned.onenote_count = noteCount;
    const rawOneNotePreview = typeof source.onenote_preview === "string" ? source.onenote_preview.trim() : "";
    if (/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(rawOneNotePreview) && rawOneNotePreview.length <= ONENOTE_PREVIEW_MAX_CHARS) {
      cleaned.onenote_preview = rawOneNotePreview;
    }
  }
  if (cleaned.counts.questions === 1 && cleaned.samples.length === 1 && source.single_question && typeof source.single_question === "object") {
    const single = source.single_question as Record<string, unknown>;
    let remaining = 1_200_000;
    const images = (value: unknown, limit: number): string[] => {
      const accepted: string[] = [];
      for (const item of (Array.isArray(value) ? value : []).slice(0, limit)) {
        if (typeof item !== "string" || item.length > 360_000 || item.length > remaining) continue;
        if (!/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(item)) continue;
        accepted.push(item);
        remaining -= item.length;
      }
      return accepted;
    };
    cleaned.single_question = {
      question_images: images(single.question_images, 12),
      figures: images(single.figures, 12),
      onenote_pages: cleaned.has_onenote_notes ? images(single.onenote_pages, 12) : [],
      images_omitted: num(single.images_omitted),
    };
  }
  return cleaned;
}

/** 付费单题的完整手写解答仍随购买权限提供，公开预览不泄露全部笔记。 */
export function previewForViewer(preview: CleanPreview | null, showNotes: boolean): CleanPreview | null {
  if (!preview?.single_question || showNotes) return preview;
  return { ...preview, single_question: { ...preview.single_question, onenote_pages: [], notes_locked: Boolean(preview.has_onenote_notes) } };
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

export function maxPackBytes(env: Env): number {
  return Number(env.MAX_PACK_MB || 50) * 1024 * 1024;
}

export function adminMaxPackMb(env: Env): number {
  const mb = Number(env.ADMIN_MAX_PACK_MB ?? DEFAULT_ADMIN_MAX_PACK_MB);
  return Number.isFinite(mb) && mb > 0 ? mb : DEFAULT_ADMIN_MAX_PACK_MB;
}

export function maxPackBytesForUser(env: Env, user: Pick<SessionUser, "role">): number {
  if (user.role === "admin") {
    return Math.max(maxPackBytes(env), adminMaxPackMb(env) * 1024 * 1024);
  }
  return maxPackBytes(env);
}

export function maxTotalR2Bytes(env: Env): number {
  const mb = Number(env.MAX_TOTAL_R2_MB || DEFAULT_MAX_TOTAL_R2_MB);
  return (Number.isFinite(mb) && mb > 0 ? mb : DEFAULT_MAX_TOTAL_R2_MB) * 1024 * 1024;
}

export function maxUserQuotaBytes(env: Env): number {
  const mb = Number(env.USER_STORAGE_QUOTA_MB || DEFAULT_USER_STORAGE_QUOTA_MB);
  return (Number.isFinite(mb) && mb > 0 ? mb : DEFAULT_USER_STORAGE_QUOTA_MB) * 1024 * 1024;
}

function maxDailyUploads(env: Env): number {
  const n = Number(env.MAX_DAILY_UPLOADS_PER_USER || DEFAULT_DAILY_UPLOADS_PER_USER);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : DEFAULT_DAILY_UPLOADS_PER_USER;
}

function r2HotTargetBytes(env: Env): number {
  const mb = Number(env.R2_HOT_TARGET_MB || DEFAULT_R2_HOT_TARGET_MB);
  return (Number.isFinite(mb) && mb > 0 ? mb : DEFAULT_R2_HOT_TARGET_MB) * 1024 * 1024;
}

function coldArchiveAgeDays(env: Env): number {
  const days = Number(env.COLD_ARCHIVE_AGE_DAYS ?? DEFAULT_COLD_ARCHIVE_AGE_DAYS);
  return Number.isFinite(days) && days >= 0 ? days : DEFAULT_COLD_ARCHIVE_AGE_DAYS;
}

function archiveBatchSize(env: Env): number {
  const n = Number(env.ARCHIVE_BATCH_SIZE || DEFAULT_ARCHIVE_BATCH_SIZE);
  return Number.isFinite(n) && n >= 1 ? Math.min(20, Math.floor(n)) : DEFAULT_ARCHIVE_BATCH_SIZE;
}

function maxPendingPerUser(env: Env): number {
  const n = Number(env.MAX_PENDING_PER_USER || DEFAULT_MAX_PENDING_PER_USER);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : DEFAULT_MAX_PENDING_PER_USER;
}

export async function activeR2Bytes(env: Env): Promise<number> {
  const row = await env.DB.prepare(
    "SELECT COALESCE(SUM(size), 0) AS total FROM packs WHERE r2_purged_at IS NULL AND r2_cached = 1 AND r2_key NOT LIKE ?",
  )
    .bind(`${WEBDAV_PREFIX}%`)
    .first<{ total: number }>();
  return Number(row?.total ?? 0);
}

export async function activeWebDavBytes(env: Env): Promise<number> {
  const row = await env.DB.prepare(
    "SELECT COALESCE(SUM(size), 0) AS total FROM packs WHERE r2_purged_at IS NULL AND (cold_key LIKE ? OR r2_key LIKE ?)",
  )
    .bind(`${WEBDAV_PREFIX}%`, `${WEBDAV_PREFIX}%`)
    .first<{ total: number }>();
  return Number(row?.total ?? 0);
}

export async function activeUserBytes(env: Env, userId: number): Promise<number> {
  const row = await env.DB.prepare("SELECT COALESCE(SUM(size), 0) AS total FROM packs WHERE author_id = ? AND r2_purged_at IS NULL")
    .bind(userId)
    .first<{ total: number }>();
  return Number(row?.total ?? 0);
}

export async function activeStorageSummary(env: Env) {
  const [r2Bytes, webdavBytes, counts] = await Promise.all([
    activeR2Bytes(env),
    activeWebDavBytes(env),
    env.DB.prepare(
      `SELECT
         SUM(CASE WHEN r2_purged_at IS NULL AND r2_cached = 1 THEN 1 ELSE 0 END) AS hot_packs,
         SUM(CASE WHEN r2_purged_at IS NULL AND cold_key IS NOT NULL AND r2_cached = 0 THEN 1 ELSE 0 END) AS cold_only_packs,
         SUM(CASE WHEN r2_purged_at IS NULL AND archive_error != '' THEN 1 ELSE 0 END) AS archive_errors
       FROM packs`,
    ).first<Record<string, number | null>>(),
  ]);
  const mode = storageMode(env);
  return {
    storage_mode: mode,
    used_bytes: mode === "webdav" ? webdavBytes : r2Bytes,
    r2_used_bytes: r2Bytes,
    webdav_used_bytes: webdavBytes,
    r2_hot_target_bytes: r2HotTargetBytes(env),
    hot_packs: Number(counts?.hot_packs ?? 0),
    cold_only_packs: Number(counts?.cold_only_packs ?? 0),
    archive_errors: Number(counts?.archive_errors ?? 0),
  };
}

export async function packBuyerCount(env: Env, packId: string): Promise<number> {
  const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM entitlements WHERE pack_id = ?")
    .bind(packId)
    .first<{ n: number }>();
  return Number(row?.n ?? 0);
}

export async function purgePackStorage(
  env: Env,
  pack: Pick<PackRow, "id" | "r2_key" | "cold_key" | "cover_key" | "r2_purged_at">,
  options: { nextStatus?: string; note?: string } = {},
): Promise<void> {
  await deleteStoredObjects(env, [pack.r2_key, pack.cold_key, pack.cover_key]);
  const now = nowIso();
  if (options.nextStatus) {
    await env.DB.prepare(
      "UPDATE packs SET status = ?, review_note = COALESCE(?, review_note), cover_key = NULL, cold_key = NULL, r2_cached = 0, archive_error = '', r2_purged_at = ?, size = 0, updated_at = ? WHERE id = ?",
    )
      .bind(options.nextStatus, options.note ?? null, now, now, pack.id)
      .run();
  } else {
    await env.DB.prepare(
      "UPDATE packs SET cover_key = NULL, cold_key = NULL, r2_cached = 0, archive_error = '', r2_purged_at = ?, size = 0, updated_at = ? WHERE id = ?",
    )
      .bind(now, now, pack.id)
      .run();
  }
}

export async function runColdArchival(
  env: Env,
  options: { nowMs?: number; forceAll?: boolean; limit?: number } = {},
): Promise<{ attempted: number; archived: number; failed: number; errors: string[] }> {
  if (!isHybridMode(env) || !hasWebDavCredentials(env)) {
    return { attempted: 0, archived: 0, failed: 0, errors: [] };
  }
  const nowMs = options.nowMs ?? Date.now();
  const forceAll = Boolean(options.forceAll);
  const limit = Math.max(1, Math.min(20, options.limit ?? archiveBatchSize(env)));
  let hotBytes = await activeR2Bytes(env);
  const targetBytes = r2HotTargetBytes(env);
  const cutoffIso = new Date(nowMs - coldArchiveAgeDays(env) * 86400 * 1000).toISOString();

  const rows = await env.DB.prepare(
    `SELECT id, r2_key, size, last_downloaded_at, created_at
       FROM packs
      WHERE status = 'approved' AND r2_cached = 1 AND r2_purged_at IS NULL AND r2_key NOT LIKE ?
      ORDER BY COALESCE(last_downloaded_at, created_at) ASC, downloads ASC
      LIMIT ?`,
  )
    .bind(`${WEBDAV_PREFIX}%`, limit * 3)
    .all<{ id: string; r2_key: string; size: number; last_downloaded_at: string | null; created_at: string }>();

  let attempted = 0;
  let archived = 0;
  let failed = 0;
  const errors: string[] = [];

  for (const row of rows.results ?? []) {
    if (attempted >= limit) break;
    const lastTouch = row.last_downloaded_at || row.created_at;
    const stale = lastTouch < cutoffIso;
    const overTarget = hotBytes > targetBytes;
    if (!forceAll && !stale && !overTarget) continue;

    attempted += 1;
    try {
      const { coldKey } = await archiveR2ObjectToWebDav(env, row.id, row.r2_key, Number(row.size || 0));
      const now = nowIso();
      await env.DB.prepare(
        "UPDATE packs SET cold_key = ?, archived_at = ?, archive_error = '', r2_cached = 0, updated_at = ? WHERE id = ?",
      )
        .bind(coldKey, now, now, row.id)
        .run();
      await deleteStoredObjects(env, [row.r2_key]).catch(() => null);
      hotBytes = Math.max(0, hotBytes - Number(row.size || 0));
      archived += 1;
    } catch (error) {
      failed += 1;
      const msg = (error instanceof Error ? error.message : "归档失败").slice(0, 200);
      errors.push(`${row.id}: ${msg}`);
      await env.DB.prepare("UPDATE packs SET archive_error = ?, updated_at = ? WHERE id = ?")
        .bind(msg, nowIso(), row.id)
        .run()
        .catch(() => null);
      if (error instanceof HttpError && (error.status === 401 || error.status === 403 || error.status === 503)) {
        break;
      }
    }
  }
  return { attempted, archived, failed, errors };
}

export async function runPackMaintenance(env: Env, nowMs = Date.now()): Promise<{
  abandoned_uploading: number;
  purged_rejected: number;
  cold_archived: number;
  cold_archive_failed: number;
}> {
  const uploadingCutoff = new Date(nowMs - 2 * 3600 * 1000).toISOString();
  const abandoned = await env.DB.prepare(
    "SELECT id, r2_key, cold_key, cover_key FROM packs WHERE status = 'uploading' AND created_at < ? LIMIT 100",
  )
    .bind(uploadingCutoff)
    .all<{ id: string; r2_key: string; cold_key: string | null; cover_key: string | null }>();
  let abandonedCount = 0;
  for (const row of abandoned.results ?? []) {
    await deleteStoredObjects(env, [row.r2_key, row.cold_key, row.cover_key]).catch(() => null);
    await env.DB.prepare("DELETE FROM packs WHERE id = ? AND status = 'uploading'").bind(row.id).run();
    abandonedCount += 1;
  }

  const rejectedCutoff = new Date(nowMs - 7 * 86400 * 1000).toISOString();
  const staleRejected = await env.DB.prepare(
    `SELECT p.id, p.r2_key, p.cold_key, p.cover_key, p.r2_purged_at
       FROM packs p
      WHERE p.status = 'rejected' AND p.r2_purged_at IS NULL AND p.updated_at < ?
        AND NOT EXISTS (SELECT 1 FROM entitlements e WHERE e.pack_id = p.id)
      LIMIT 100`,
  )
    .bind(rejectedCutoff)
    .all<Pick<PackRow, "id" | "r2_key" | "cold_key" | "cover_key" | "r2_purged_at">>();
  let purgedCount = 0;
  for (const row of staleRejected.results ?? []) {
    await purgePackStorage(env, row);
    purgedCount += 1;
  }

  const archival = await runColdArchival(env, { nowMs });
  return {
    abandoned_uploading: abandonedCount,
    purged_rejected: purgedCount,
    cold_archived: archival.archived,
    cold_archive_failed: archival.failed,
  };
}

function uploadRateKey(userId: number): string {
  return `pack-upload:${userId}`;
}

async function assertUploadAllowed(env: Env, user: SessionUser, incomingBytes: number): Promise<void> {
  const isAdmin = user.role === "admin";
  if (!isAdmin) {
    if (await rateLimited(env, uploadRateKey(user.id), maxDailyUploads(env), 24 * 3600 * 1000)) {
      fail(429, `普通用户每天最多上传 ${maxDailyUploads(env)} 个题包，明天再试。`);
    }
    const pendingRow = await env.DB.prepare("SELECT COUNT(*) AS n FROM packs WHERE author_id = ? AND status = 'pending'")
      .bind(user.id)
      .first<{ n: number }>();
    const pendingLimit = maxPendingPerUser(env);
    if (Number(pendingRow?.n ?? 0) >= pendingLimit) {
      fail(429, `你有 ${pendingLimit} 个待审题包正在排队审核，等管理员处理后再继续上传。`);
    }
    const userUsed = await activeUserBytes(env, user.id);
    const userQuota = maxUserQuotaBytes(env);
    if (userUsed + incomingBytes > userQuota) {
      const quotaMb = Math.round(userQuota / 1024 / 1024);
      const quotaText = quotaMb >= 1024 ? `${(quotaMb / 1024).toFixed(1)} GB` : `${quotaMb} MB`;
      fail(413, `你的个人云端累计空间已达上限（${quotaText}），请先在「我的上传」清理未售旧题包释放空间。`);
    }
  }
  if (!isWebDavMode(env)) {
    const used = await activeR2Bytes(env);
    const limit = maxTotalR2Bytes(env);
    if (used + incomingBytes > limit) {
      fail(507, "社区 R2 热层空间已接近上限，请稍后再试或联系管理员清理无效文件。");
    }
  } else if (env.MAX_TOTAL_WEBDAV_MB) {
    const webdavMb = Number(env.MAX_TOTAL_WEBDAV_MB);
    if (Number.isFinite(webdavMb) && webdavMb > 0) {
      const used = await activeWebDavBytes(env);
      if (used + incomingBytes > webdavMb * 1024 * 1024) {
        fail(507, "社区网盘总空间已接近上限，请联系管理员清理或扩容。");
      }
    }
  }
}

type PackMetaInput = {
  title: unknown;
  stage: unknown;
  subject: unknown;
  year: unknown;
  tags: unknown;
  price_yuan: unknown;
  originality: unknown;
  description: unknown;
  rights_confirmed: unknown;
  originals_confirmed: unknown;
};

type ParsedPackMeta = {
  title: string;
  stage: string;
  subject: string;
  year: number | null;
  tags: string[];
  price: number;
  originality: "original" | "compiled";
  description: string;
  originalsConfirmed: boolean;
};

function validatePackMetaInput(input: PackMetaInput): ParsedPackMeta {
  const confirmed = input.rights_confirmed === "1" || input.rights_confirmed === true;
  if (!confirmed) fail(400, "请先确认你有权分发这份题包。");
  const title = text(input.title, 80);
  if (title.length < 2) fail(400, "请填一个标题。");
  const stage = text(input.stage, 10);
  if (stage && !STAGES.includes(stage)) fail(400, "学段只能选小学、初中、高中、大学或其他。");
  const subject = text(input.subject, 20) || "数学";
  const year = intParam(input.year, 0, 0, 3000) || null;
  const tags = text(input.tags, 300)
    .split(/[,，、\s]+/)
    .map((tag) => tag.trim())
    .filter(Boolean)
    .slice(0, 12);
  const price = parsePriceYuan(input.price_yuan);
  const originality = input.originality === "original" ? "original" : "compiled";
  const description = text(input.description, 5000);
  const originalsConfirmed = input.originals_confirmed === "1" || input.originals_confirmed === true;
  return { title, stage, subject, year, tags, price, originality, description, originalsConfirmed };
}

function validateManifestAndPreview(
  rawManifest: unknown,
  rawPreview: unknown,
  originalsConfirmed: boolean,
): CleanPreview {
  const manifest = (rawManifest && typeof rawManifest === "object" ? rawManifest : null) as Record<
    string,
    unknown
  > | null;
  if (!manifest) fail(400, "题包清单读不出来。");
  if (manifest.format !== 1) fail(400, "题包格式版本不认识，请升级题有据后重新导出。");
  if (!manifest.community || !rawPreview || typeof rawPreview !== "object") {
    fail(400, "请用题有据桌面版的「导出为社区题包」重新导出，普通备份包不能直接发到社区。");
  }
  const preview = cleanPreview(rawPreview);
  const community = manifest.community as Record<string, unknown>;
  preview.include_originals = Boolean(community.include_originals);
  if (preview.include_originals && !originalsConfirmed) {
    fail(400, "这份题包带了原卷文件，请再确认原卷版权归你或已获授权。");
  }
  return preview;
}

function sniffCoverBytes(bytes: Uint8Array): { ext: string; contentType: string } | null {
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

function decodeCoverBase64(raw: unknown): ParsedPack["cover"] {
  if (typeof raw !== "string" || !raw.trim()) return null;
  const cleaned = raw.trim().replace(/^data:image\/[a-zA-Z0-9+.-]+;base64,/, "");
  let binary: string;
  try {
    binary = atob(cleaned);
  } catch {
    fail(400, "封面图片格式不正确。");
  }
  if (binary.length > COVER_MAX_BYTES) fail(413, "封面图片不能超过 1 MB。");
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  const sniffed = sniffCoverBytes(bytes);
  if (!sniffed) fail(400, "封面图片只支持 PNG、JPG 或 WebP。");
  return { bytes, contentType: sniffed.contentType, ext: sniffed.ext };
}

export async function loadPack(env: Env, id: string): Promise<PackRow | null> {
  return (
    (await env.DB.prepare(
      `SELECT id, author_id, title, status, price_cents, originality, include_originals,
              r2_key, cold_key, cover_key, r2_purged_at, r2_cached, last_downloaded_at, archived_at, archive_error, size
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
            CASE WHEN v.preview_json LIKE '%"has_onenote_notes":true%' THEN 1 ELSE 0 END AS has_onenote_notes,
            u.username AS author_username, u.display_name AS author_name
       FROM packs p
       JOIN users u ON u.id = p.author_id
       LEFT JOIN pack_previews v ON v.pack_id = p.id
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

async function insertPackRecord(
  env: Env,
  params: {
    id: string;
    userId: number;
    meta: ParsedPackMeta;
    preview: CleanPreview;
    r2Key: string;
    coverKey: string | null;
    size: number;
    status: "uploading" | "pending";
  },
) {
  const { id, userId, meta, preview, r2Key, coverKey, size, status } = params;
  const tagNames = meta.tags.length ? meta.tags : preview.tags.slice(0, 8).map((tag) => tag.name);
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO packs (id, author_id, title, description, stage, subject, year, tags_text, price_cents,
                          originality, status, r2_key, cover_key, size, question_count, paper_count,
                          include_originals, rights_confirmed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      id,
      userId,
      meta.title,
      meta.description,
      meta.stage,
      meta.subject,
      meta.year,
      tagNames.join(","),
      meta.price,
      meta.originality,
      status,
      r2Key,
      coverKey,
      size,
      preview.counts.questions,
      preview.counts.papers,
      preview.include_originals ? 1 : 0,
      nowIso(),
    ),
    env.DB.prepare("INSERT INTO pack_previews (pack_id, preview_json) VALUES (?, ?)").bind(
      id,
      JSON.stringify(preview),
    ),
  ]);
}

packRoutes.post("/packs/init", async (c) => {
  const user = requireUser(c);
  const body = await readJson(c);
  const maxBytes = maxPackBytesForUser(c.env, user);
  const maxMbLabel = Math.round(maxBytes / 1024 / 1024);
  const fileSize = intParam(body.file_size, 0, 0, Number.MAX_SAFE_INTEGER);
  if (fileSize < 22) fail(400, "请选择有效的社区题包 zip 文件。");
  if (fileSize > maxBytes) fail(413, `题包太大了，单个不能超过 ${maxMbLabel} MB。`);

  const meta = validatePackMetaInput({
    title: body.title,
    stage: body.stage,
    subject: body.subject,
    year: body.year,
    tags: body.tags,
    price_yuan: body.price_yuan,
    originality: body.originality,
    description: body.description,
    rights_confirmed: body.rights_confirmed,
    originals_confirmed: body.originals_confirmed,
  });
  const preview = validateManifestAndPreview(body.manifest, body.preview, meta.originalsConfirmed);
  if (meta.price > 0) {
    await assertCanSell(c.env, {
      author_id: user.id,
      originality: meta.originality,
      include_originals: preview.include_originals,
    });
  }
  await assertUploadAllowed(c.env, user, fileSize);
  const cover = decodeCoverBase64(body.cover_base64);

  const id = crypto.randomUUID();
  const r2Key = packStorageKey(c.env, id);
  let coverKey: string | null = null;
  if (cover) {
    coverKey = `covers/${id}.${cover.ext}`;
    await c.env.PACKS.put(coverKey, cover.bytes, { httpMetadata: { contentType: cover.contentType } });
  }
  try {
    await insertPackRecord(c.env, {
      id,
      userId: user.id,
      meta,
      preview,
      r2Key,
      coverKey,
      size: fileSize,
      status: "uploading",
    });
  } catch (error) {
    if (coverKey) await c.env.PACKS.delete(coverKey).catch(() => null);
    throw error;
  }
  return c.json({ id, status: "uploading", upload_url: `/api/packs/${id}/bundle` }, 201);
});

packRoutes.put("/packs/:id/bundle", async (c) => {
  const user = requireUser(c);
  const pack = await loadPack(c.env, c.req.param("id"));
  if (!pack || pack.author_id !== user.id) fail(404, "没有这份题包。");
  if (pack.status !== "uploading") fail(409, "这份题包已经完成上传，不能重复覆盖。");
  const maxBytes = maxPackBytesForUser(c.env, user);
  const maxMbLabel = Math.round(maxBytes / 1024 / 1024);
  if (pack.size <= 0 || pack.size > maxBytes) fail(413, `题包太大了，单个不能超过 ${maxMbLabel} MB。`);

  const declaredHeader = c.req.header("Content-Length");
  if (declaredHeader !== undefined) {
    const declared = Number(declaredHeader);
    if (!Number.isFinite(declared) || declared !== pack.size) {
      fail(400, "上传的文件大小和初始化记录不一致，请重试。");
    }
  }
  const sourceBody = c.req.raw.body;
  if (!sourceBody) fail(400, "没有收到题包文件内容。");

  let total = 0;
  let checkedHeader = false;
  let firstByte: number | null = null;
  const verifier = new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      if (!checkedHeader && chunk.byteLength > 0) {
        if (firstByte === null && chunk.byteLength === 1) {
          firstByte = chunk[0];
        } else {
          const b0 = firstByte !== null ? firstByte : chunk[0];
          const b1 = firstByte !== null ? chunk[0] : chunk[1];
          if (b0 !== 0x50 || b1 !== 0x4b) {
            throw new HttpError(400, "这个文件不是有效的 zip 题包。");
          }
          checkedHeader = true;
        }
      }
      total += chunk.byteLength;
      if (total > pack.size) {
        throw new HttpError(413, "实际上传的文件超过了登记大小。");
      }
      controller.enqueue(chunk);
    },
    flush() {
      if (!checkedHeader || total !== pack.size) {
        throw new HttpError(400, "题包上传不完整，请重试。");
      }
    },
  });

  const verifiedStream = sourceBody.pipeThrough(verifier);
  const FixedStream = (globalThis as { FixedLengthStream?: new (len: number) => TransformStream<Uint8Array, Uint8Array> })
    .FixedLengthStream;
  const uploadStream =
    typeof FixedStream === "function" ? verifiedStream.pipeThrough(new FixedStream(pack.size)) : verifiedStream;

  try {
    await putPackObject(c.env, pack.r2_key, uploadStream as unknown as ReadableStream<Uint8Array>, pack.size, user.id);
  } catch (error) {
    await deleteStoredObjects(c.env, [pack.r2_key]).catch(() => null);
    if (error instanceof HttpError) throw error;
    fail(400, "题包流式写入失败，请重新上传。");
  }

  await c.env.DB.prepare("UPDATE packs SET status = 'pending', updated_at = ? WHERE id = ? AND status = 'uploading'")
    .bind(nowIso(), pack.id)
    .run();
  if (user.role !== "admin") await hit(c.env, uploadRateKey(user.id));
  return c.json({ id: pack.id, status: "pending" });
});

packRoutes.post("/packs", async (c) => {
  const user = requireUser(c);
  const maxBytes = maxPackBytes(c.env);
  const directLimit = Math.min(maxBytes, DIRECT_MULTIPART_MAX_BYTES);
  const declared = Number(c.req.header("Content-Length") || 0);
  if (declared > maxBytes + 64 * 1024) fail(413, `题包太大了，单个不能超过 ${c.env.MAX_PACK_MB || 50} MB。`);
  if (declared > directLimit + 64 * 1024) {
    fail(413, "题包超过 20 MB，请使用网页端分段流式上传以免超出服务内存。");
  }
  const form = await c.req.parseBody();
  const file = form.file;
  if (!(file instanceof File)) fail(400, "请选择一个题包 zip 文件。");
  if (file.size > maxBytes) fail(413, `题包太大了，单个不能超过 ${c.env.MAX_PACK_MB || 50} MB。`);
  if (file.size > directLimit) {
    fail(413, "题包超过 20 MB，请使用网页端分段流式上传以免超出服务内存。");
  }

  const meta = validatePackMetaInput({
    title: form.title,
    stage: form.stage,
    subject: form.subject,
    year: form.year,
    tags: form.tags,
    price_yuan: form.price_yuan,
    originality: form.originality,
    description: form.description,
    rights_confirmed: form.rights_confirmed,
    originals_confirmed: form.originals_confirmed,
  });

  const bytes = new Uint8Array(await file.arrayBuffer());
  const parsed = parsePackZip(bytes);
  if (parsed.preview.include_originals && !meta.originalsConfirmed) {
    fail(400, "这份题包带了原卷文件，请再确认原卷版权归你或已获授权。");
  }
  if (meta.price > 0) {
    await assertCanSell(c.env, {
      author_id: user.id,
      originality: meta.originality,
      include_originals: parsed.preview.include_originals,
    });
  }
  await assertUploadAllowed(c.env, user, bytes.byteLength);

  const id = crypto.randomUUID();
  const r2Key = packStorageKey(c.env, id);
  await putPackObject(c.env, r2Key, bytes, bytes.byteLength, user.id);
  let coverKey: string | null = null;
  if (parsed.cover) {
    coverKey = `covers/${id}.${parsed.cover.ext}`;
    await c.env.PACKS.put(coverKey, parsed.cover.bytes, { httpMetadata: { contentType: parsed.cover.contentType } });
  }
  try {
    await insertPackRecord(c.env, {
      id,
      userId: user.id,
      meta,
      preview: parsed.preview,
      r2Key,
      coverKey,
      size: bytes.byteLength,
      status: "pending",
    });
  } catch (error) {
    await deleteStoredObjects(c.env, [r2Key, coverKey]).catch(() => null);
    throw error;
  }
  if (user.role !== "admin") await hit(c.env, uploadRateKey(user.id));
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
  const buyers = manage ? await packBuyerCount(c.env, row.id) : undefined;
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
  const { preview_json: previewJson, r2_key: _r2, cold_key: _cold, rights_confirmed_at: _rights, ...rest } = row;
  return c.json({
    pack: {
      ...rest,
      cover_key: undefined,
      has_cover: Boolean(row.cover_key),
      r2_purged_at: row.r2_purged_at ?? null,
      buyers,
      review_note: manage ? row.review_note : undefined,
      reviewed_by: undefined,
    },
    preview: previewForViewer(previewJson ? JSON.parse(String(previewJson)) : null, !paid || owned),
    viewer: {
      can_manage: manage,
      owned,
      can_download: !row.r2_purged_at && row.status === "approved" && (!paid || owned),
    },
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
  if (pack.r2_purged_at) fail(410, "这份题包文件已被清理。");
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
  if (pack.r2_purged_at) fail(410, "这份题包文件已被清理。");

  let responseStream: ReadableStream<Uint8Array> | null = null;
  if (pack.r2_cached !== 0 && !isWebDavKey(pack.r2_key)) {
    const r2Obj = await c.env.PACKS.get(pack.r2_key);
    if (r2Obj?.body) {
      responseStream = r2Obj.body as unknown as ReadableStream<Uint8Array>;
    }
  }

  if (!responseStream) {
    const fallbackKey = pack.cold_key || (isWebDavKey(pack.r2_key) ? pack.r2_key : pack.r2_key);
    const coldStream = await getPackStream(c.env, fallbackKey);
    if (!coldStream) fail(404, "题包文件找不到了，请联系作者。");

    if (isHybridMode(c.env) && !isWebDavKey(pack.r2_key)) {
      const usedHot = await activeR2Bytes(c.env);
      if (usedHot + pack.size < maxTotalR2Bytes(c.env) * 0.9) {
        const [clientStream, rewarmStream] = coldStream.tee();
        responseStream = clientStream;
        const FixedStream = (
          globalThis as { FixedLengthStream?: new (len: number) => TransformStream<Uint8Array, Uint8Array> }
        ).FixedLengthStream;
        const r2RewarmBody =
          typeof FixedStream === "function" && pack.size > 0
            ? rewarmStream.pipeThrough(new FixedStream(pack.size))
            : rewarmStream;
        const rewarmTask = putPackObject(
          c.env,
          pack.r2_key,
          r2RewarmBody as unknown as ReadableStream<Uint8Array>,
          pack.size,
          pack.author_id,
        )
          .then(() =>
            c.env.DB.prepare("UPDATE packs SET r2_cached = 1, updated_at = ? WHERE id = ?")
              .bind(nowIso(), pack.id)
              .run(),
          )
          .catch(() => null);
        try {
          c.executionCtx.waitUntil(rewarmTask);
        } catch {
          await rewarmTask;
        }
      } else {
        responseStream = coldStream;
      }
    } else {
      responseStream = coldStream;
    }
  }

  const now = nowIso();
  await c.env.DB.prepare("UPDATE packs SET downloads = downloads + 1, last_downloaded_at = ? WHERE id = ?")
    .bind(now, pack.id)
    .run();
  const safeName = `${pack.title.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "_").slice(0, 60) || "题包"}.zip`;
  return new Response(responseStream as unknown as ReadableStream, {
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": `attachment; filename="tiyouju-pack.zip"; filename*=UTF-8''${encodeURIComponent(safeName)}`,
      "Cache-Control": "private, no-store",
      ...(pack.size > 0 ? { "Content-Length": String(pack.size) } : {}),
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

packRoutes.delete("/packs/:id", async (c) => {
  const user = requireUser(c);
  const pack = await loadPack(c.env, c.req.param("id"));
  if (!pack || !canManage(user, pack)) fail(404, "没有这份题包。");
  if (pack.status === "taken_down" && user.role !== "admin") {
    fail(403, "这份题包已被管理员下架，不能自行删除。");
  }
  const buyers = await packBuyerCount(c.env, pack.id);
  if (buyers > 0) {
    fail(409, "已有买家拥有这份题包，不能删除存储文件；如需停售请点「撤回题包」。");
  }
  await purgePackStorage(c.env, pack, { nextStatus: "withdrawn" });
  return c.json({ ok: true, purged: true });
});

packRoutes.get("/me/packs", async (c) => {
  const user = requireUser(c);
  const mine = await c.env.DB.prepare(
    `SELECT p.id, p.title, p.status, p.review_note, p.price_cents, p.originality, p.question_count, p.downloads,
            p.size, p.r2_purged_at, p.created_at,
            (SELECT COUNT(*) FROM entitlements e WHERE e.pack_id = p.id) AS buyers,
            (SELECT COALESCE(SUM(e.price_cents), 0) FROM entitlements e WHERE e.pack_id = p.id) AS estimated_cents,
            (SELECT COUNT(*) FROM purchase_requests r WHERE r.pack_id = p.id AND r.status = 'pending') AS pending_requests
       FROM packs p WHERE p.author_id = ? AND p.status != 'uploading' ORDER BY p.created_at DESC`,
  )
    .bind(user.id)
    .all();
  const owned = await c.env.DB.prepare(
    `SELECT p.id, p.title, p.status, p.price_cents, p.question_count, p.r2_purged_at, e.created_at AS redeemed_at, e.source,
            u.display_name AS author_name
       FROM entitlements e JOIN packs p ON p.id = e.pack_id JOIN users u ON u.id = p.author_id
      WHERE e.user_id = ? ORDER BY e.created_at DESC`,
  )
    .bind(user.id)
    .all();
  const usedBytes = await activeUserBytes(c.env, user.id);
  const quotaBytes = maxUserQuotaBytes(c.env);
  return c.json({
    packs: mine.results ?? [],
    owned: owned.results ?? [],
    storage: {
      used_bytes: usedBytes,
      quota_bytes: user.role === "admin" ? null : quotaBytes,
      is_unlimited: user.role === "admin",
    },
  });
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
