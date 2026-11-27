import type { Env, PackStorageKey } from "./env.ts";
import { HttpError, fail } from "./util.ts";

export type { PackStorageKey };

export const WEBDAV_PREFIX = "webdav:";
const ensuredDirs = new Set<string>();

export function isWebDavMode(env: Env): boolean {
  return String(env.PACK_STORAGE_MODE || "r2").trim().toLowerCase() === "webdav";
}

export function isWebDavKey(key: string | null | undefined): boolean {
  return typeof key === "string" && key.startsWith(WEBDAV_PREFIX);
}

export function packStorageKey(env: Env, packId: string): string {
  const cleanId = String(packId).trim();
  const baseKey = `packs/${cleanId}.zip`;
  return isWebDavMode(env) ? `${WEBDAV_PREFIX}${baseKey}` : baseKey;
}

function webdavConfig(env: Env): { baseUrl: string; dir: string; authHeader: string } {
  const rawBase = String(env.WEBDAV_BASE_URL || "").trim().replace(/\/+$/, "");
  const username = String(env.WEBDAV_USERNAME || "").trim();
  const password = String(env.WEBDAV_PASSWORD || "");
  if (!rawBase || !username || !password) {
    fail(503, "社区网盘存储尚未完成服务器配置（缺少 WebDAV 地址或密钥）。");
  }
  const dir = String(env.WEBDAV_DIR || "tiyouju-packs")
    .trim()
    .replace(/^\/+|\/+$/g, "");
  const rawAuth = new TextEncoder().encode(`${username}:${password}`);
  let binary = "";
  for (let i = 0; i < rawAuth.length; i += 1) binary += String.fromCharCode(rawAuth[i]);
  return {
    baseUrl: rawBase,
    dir,
    authHeader: `Basic ${btoa(binary)}`,
  };
}

function encodePathSegments(relPath: string): string {
  return relPath
    .split("/")
    .filter(Boolean)
    .map((seg) => encodeURIComponent(seg))
    .join("/");
}

function webdavObjectUrl(env: Env, storageKey: string): { url: string; dirUrls: string[]; authHeader: string } {
  const { baseUrl, dir, authHeader } = webdavConfig(env);
  const inner = isWebDavKey(storageKey) ? storageKey.slice(WEBDAV_PREFIX.length) : storageKey;
  const fullRel = dir ? `${dir}/${inner}` : inner;
  const parts = fullRel.split("/").filter(Boolean);
  const dirUrls: string[] = [];
  for (let i = 1; i < parts.length; i += 1) {
    dirUrls.push(`${baseUrl}/${encodePathSegments(parts.slice(0, i).join("/"))}`);
  }
  return {
    url: `${baseUrl}/${encodePathSegments(fullRel)}`,
    dirUrls,
    authHeader,
  };
}

function fetcherOf(env: Env): typeof fetch {
  return typeof env.WEBDAV_FETCH === "function" ? env.WEBDAV_FETCH : fetch;
}

async function ensureWebDavDirectories(env: Env, dirUrls: string[], authHeader: string): Promise<void> {
  const doFetch = fetcherOf(env);
  for (const dirUrl of dirUrls) {
    if (ensuredDirs.has(dirUrl)) continue;
    const resp = await doFetch(dirUrl, {
      method: "MKCOL",
      headers: { Authorization: authHeader },
    }).catch(() => null);
    if (resp && (resp.ok || resp.status === 405 || resp.status === 301 || resp.status === 409)) {
      ensuredDirs.add(dirUrl);
    } else if (resp && (resp.status === 401 || resp.status === 403)) {
      fail(503, "网盘存储认证失败，请管理员检查 WebDAV 账号和应用密码。");
    } else if (resp && resp.status === 429) {
      fail(503, "网盘接口请求过于频繁，请稍后再试。");
    }
  }
}

export async function putPackObject(
  env: Env,
  storageKey: string,
  body: ReadableStream<Uint8Array> | Uint8Array,
  size: number,
  authorId: number,
): Promise<void> {
  if (!isWebDavKey(storageKey)) {
    await env.PACKS.put(storageKey, body as never, {
      httpMetadata: { contentType: "application/zip" },
      customMetadata: { author: String(authorId) },
    });
    return;
  }
  const { url, dirUrls, authHeader } = webdavObjectUrl(env, storageKey);
  await ensureWebDavDirectories(env, dirUrls, authHeader);
  const doFetch = fetcherOf(env);
  let resp: Response;
  try {
    resp = await doFetch(url, {
      method: "PUT",
      headers: {
        Authorization: authHeader,
        "Content-Type": "application/zip",
        "Content-Length": String(size),
      },
      body: body as BodyInit,
      ...(body instanceof Uint8Array ? {} : ({ duplex: "half" } as Record<string, unknown>)),
    });
  } catch (error) {
    if (error instanceof HttpError) throw error;
    fail(502, "写入网盘存储失败，请稍后重试。");
  }
  if (!resp.ok) {
    if (resp.status === 429) fail(503, "网盘上传请求过于频繁，请几秒后再试。");
    if (resp.status === 401 || resp.status === 403) fail(503, "网盘存储认证失败，请联系管理员检查配置。");
    if (resp.status === 507) fail(507, "网盘存储空间已满，请联系管理员清理。");
    fail(502, `网盘写入返回异常（HTTP ${resp.status}），请稍后重试。`);
  }
}

export async function getPackStream(env: Env, storageKey: string): Promise<ReadableStream<Uint8Array> | null> {
  if (!isWebDavKey(storageKey)) {
    const object = await env.PACKS.get(storageKey);
    return object ? (object.body as unknown as ReadableStream<Uint8Array>) : null;
  }
  const { url, authHeader } = webdavObjectUrl(env, storageKey);
  const doFetch = fetcherOf(env);
  let resp: Response;
  try {
    resp = await doFetch(url, {
      method: "GET",
      headers: { Authorization: authHeader },
    });
  } catch {
    fail(502, "从网盘读取题包失败，请稍后重试。");
  }
  if (resp.status === 404) return null;
  if (resp.status === 429) fail(503, "网盘当前下载较忙，请几秒后再点一次。");
  if (!resp.ok || !resp.body) fail(502, `网盘读取失败（HTTP ${resp.status}），请稍后重试。`);
  return resp.body as unknown as ReadableStream<Uint8Array>;
}

export async function deleteStoredObjects(env: Env, keys: (string | null | undefined)[]): Promise<void> {
  const r2Keys: string[] = [];
  const webdavKeys: string[] = [];
  for (const key of keys) {
    if (!key) continue;
    if (isWebDavKey(key)) webdavKeys.push(key);
    else r2Keys.push(key);
  }
  if (r2Keys.length) {
    await env.PACKS.delete(r2Keys);
  }
  if (webdavKeys.length) {
    const doFetch = fetcherOf(env);
    for (const key of webdavKeys) {
      const { url, authHeader } = webdavObjectUrl(env, key);
      await doFetch(url, {
        method: "DELETE",
        headers: { Authorization: authHeader },
      }).catch(() => null);
    }
  }
}
