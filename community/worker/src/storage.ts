import type { Env, PackStorageKey } from "./env.ts";
import { HttpError, fail } from "./util.ts";

export type { PackStorageKey };

export const WEBDAV_PREFIX = "webdav:";
const ensuredDirs = new Set<string>();

export function storageMode(env: Env): "r2" | "webdav" | "hybrid" {
  const mode = String(env.PACK_STORAGE_MODE || "r2").trim().toLowerCase();
  if (mode === "hybrid") return "hybrid";
  if (mode === "webdav") return "webdav";
  return "r2";
}

export function isWebDavMode(env: Env): boolean {
  return storageMode(env) === "webdav";
}

export function isHybridMode(env: Env): boolean {
  return storageMode(env) === "hybrid";
}

export function hasWebDavCredentials(env: Env): boolean {
  const base = String(env.WEBDAV_BASE_URL || "").trim();
  const user = String(env.WEBDAV_USERNAME || "").trim();
  const pass = String(env.WEBDAV_PASSWORD || "");
  return Boolean(base && user && pass);
}

export function isWebDavKey(key: string | null | undefined): boolean {
  return typeof key === "string" && key.startsWith(WEBDAV_PREFIX);
}

export function packStorageKey(env: Env, packId: string): string {
  const cleanId = String(packId).trim();
  const baseKey = `packs/${cleanId}.zip`;
  return isWebDavMode(env) ? `${WEBDAV_PREFIX}${baseKey}` : baseKey;
}

export function coldWebDavKey(packId: string): string {
  return `${WEBDAV_PREFIX}packs/${String(packId).trim()}.zip`;
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

export function webdavObjectUrl(env: Env, storageKey: string): { url: string; dirUrls: string[]; authHeader: string; relPath: string } {
  const { baseUrl, dir, authHeader } = webdavConfig(env);
  const inner = isWebDavKey(storageKey) ? storageKey.slice(WEBDAV_PREFIX.length) : storageKey;
  const fullRel = dir ? `${dir}/${inner}` : inner;
  const parts = fullRel.split("/").filter(Boolean);
  const dirUrls: string[] = [];
  for (let i = 1; i < parts.length; i += 1) {
    dirUrls.push(`${baseUrl}/${encodePathSegments(parts.slice(0, i).join("/"))}/`);
  }
  return {
    url: `${baseUrl}/${encodePathSegments(fullRel)}`,
    dirUrls,
    authHeader,
    relPath: fullRel,
  };
}

function fetcherOf(env: Env): typeof fetch {
  return typeof env.WEBDAV_FETCH === "function" ? env.WEBDAV_FETCH : fetch;
}

function delayMs(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchWithRetry(
  doFetch: typeof fetch,
  url: string,
  init: RequestInit,
  options: { retries?: number } = {},
): Promise<Response> {
  const retries = options.retries ?? 1;
  let lastResp: Response | null = null;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      const resp = await doFetch(url, init);
      if ((resp.status === 429 || resp.status >= 502) && attempt < retries) {
        lastResp = resp;
        await delayMs(200 * (attempt + 1));
        continue;
      }
      return resp;
    } catch (error) {
      if (attempt < retries) {
        await delayMs(200 * (attempt + 1));
        continue;
      }
      if (lastResp) return lastResp;
      throw error;
    }
  }
  return lastResp as Response;
}

async function ensureWebDavDirectories(env: Env, dirUrls: string[], authHeader: string): Promise<void> {
  const doFetch = fetcherOf(env);
  const useCache = typeof env.WEBDAV_FETCH !== "function";
  for (const rawDirUrl of dirUrls) {
    const dirUrl = rawDirUrl.endsWith("/") ? rawDirUrl : `${rawDirUrl}/`;
    if (useCache && ensuredDirs.has(dirUrl)) continue;
    let resp = await fetchWithRetry(
      doFetch,
      dirUrl,
      { method: "MKCOL", headers: { Authorization: authHeader }, redirect: "manual" },
      { retries: retriesCount(env) },
    ).catch(() => null);

    if (resp && (resp.status === 301 || resp.status === 302 || resp.status === 308)) {
      const loc = resp.headers.get("Location");
      if (loc) {
        const nextUrl = new URL(loc, dirUrl).toString();
        resp = await fetchWithRetry(
          doFetch,
          nextUrl,
          { method: "MKCOL", headers: { Authorization: authHeader }, redirect: "manual" },
          { retries: 0 },
        ).catch(() => resp);
      }
    }

    if (!resp) {
      fail(502, "连接网盘创建目录失败，请稍后重试。");
    }
    if (resp.status === 401 || resp.status === 403) {
      fail(503, "网盘存储认证失败，请管理员检查 WebDAV 账号和应用密码。");
    }
    if (resp.status === 429) {
      fail(503, "网盘接口请求过于频繁，请稍后再试。");
    }
    if (resp.ok || resp.status === 201 || resp.status === 204 || resp.status === 405 || resp.status === 409) {
      ensuredDirs.add(dirUrl);
      continue;
    }
    fail(502, `网盘创建目录返回异常（HTTP ${resp.status}）。`);
  }
}

function retriesCount(env: Env): number {
  return typeof env.WEBDAV_FETCH === "function" ? 1 : 1;
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

export async function headWebDavObject(
  env: Env,
  storageKey: string,
): Promise<{ ok: boolean; status: number; contentLength: number | null }> {
  const { url, authHeader } = webdavObjectUrl(env, storageKey);
  const doFetch = fetcherOf(env);
  const resp = await fetchWithRetry(doFetch, url, { method: "HEAD", headers: { Authorization: authHeader } }, { retries: 1 });
  if (!resp.ok) {
    return { ok: false, status: resp.status, contentLength: null };
  }
  const rawLen = resp.headers.get("Content-Length");
  const parsedLen = rawLen !== null && rawLen.trim() !== "" ? Number(rawLen) : NaN;
  return {
    ok: true,
    status: resp.status,
    contentLength: Number.isFinite(parsedLen) && parsedLen >= 0 ? parsedLen : null,
  };
}

export async function archiveR2ObjectToWebDav(
  env: Env,
  packId: string,
  r2Key: string,
  expectedSize: number,
): Promise<{ coldKey: string; verifiedSize: number }> {
  const obj = await env.PACKS.get(r2Key);
  if (!obj || !obj.body) {
    fail(404, "R2 热层缺少该题包源文件，跳过归档。");
  }
  const bytes = new Uint8Array(await new Response(obj.body as unknown as ReadableStream).arrayBuffer());
  if (bytes.byteLength <= 0 || (expectedSize > 0 && bytes.byteLength !== expectedSize)) {
    fail(500, `R2 源文件大小异常（期望 ${expectedSize}，实际 ${bytes.byteLength}）。`);
  }

  const coldKey = coldWebDavKey(packId);
  const { url, dirUrls, authHeader } = webdavObjectUrl(env, coldKey);
  await ensureWebDavDirectories(env, dirUrls, authHeader);
  const doFetch = fetcherOf(env);
  let putResp: Response;
  try {
    putResp = await fetchWithRetry(
      doFetch,
      url,
      {
        method: "PUT",
        headers: {
          Authorization: authHeader,
          "Content-Type": "application/zip",
          "Content-Length": String(bytes.byteLength),
        },
        body: bytes as unknown as BodyInit,
      },
      { retries: 1 },
    );
  } catch (error) {
    if (error instanceof HttpError) throw error;
    fail(502, "写入 123 云盘冷库时网络异常。");
  }
  if (!putResp.ok) {
    if (putResp.status === 429) fail(503, "123 云盘接口限流 (429)，稍后自动重试。");
    if (putResp.status === 401 || putResp.status === 403) fail(503, "123 云盘 WebDAV 认证失败，请检查账号或应用密码。");
    fail(502, `123 云盘 PUT 返回 HTTP ${putResp.status}`);
  }

  const verified = await headWebDavObject(env, coldKey);
  if (!verified.ok) {
    fail(502, `123 云盘落盘核验失败（HEAD HTTP ${verified.status}），已保留 R2 热副本。`);
  }
  if (verified.contentLength !== null && verified.contentLength !== bytes.byteLength) {
    fail(502, `123 云盘文件大小不一致（远端 ${verified.contentLength} != 实际 ${bytes.byteLength}），已保留 R2 热副本。`);
  }
  return { coldKey, verifiedSize: bytes.byteLength };
}

export async function probeWebDav(
  env: Env,
): Promise<{ ok: boolean; status: number; target_dir: string; error?: string }> {
  const probeKey = coldWebDavKey("probe-check");
  try {
    const { dirUrls, authHeader, relPath } = webdavObjectUrl(env, probeKey);
    const parentDir = relPath.split("/").slice(0, -1).join("/");
    await ensureWebDavDirectories(env, dirUrls, authHeader);
    const doFetch = fetcherOf(env);
    const packsDirUrl = dirUrls[dirUrls.length - 1];
    const resp = await fetchWithRetry(
      doFetch,
      packsDirUrl,
      { method: "PROPFIND", headers: { Authorization: authHeader, Depth: "0" } },
      { retries: 0 },
    ).catch(() => null);
    if (!resp) {
      return { ok: false, status: 502, target_dir: parentDir, error: "无法连接 123 云盘 WebDAV 服务。" };
    }
    if (resp.status === 401 || resp.status === 403) {
      return { ok: false, status: resp.status, target_dir: parentDir, error: "WebDAV 认证失败，请检查账号或应用密码。" };
    }
    if (resp.ok || resp.status === 207 || resp.status === 405) {
      return { ok: true, status: resp.status, target_dir: parentDir };
    }
    return { ok: false, status: resp.status, target_dir: parentDir, error: `PROPFIND 返回 HTTP ${resp.status}` };
  } catch (err) {
    const msg = err instanceof Error ? err.message : "WebDAV 探测失败";
    const status = err instanceof HttpError ? err.status : 502;
    return { ok: false, status, target_dir: String(env.WEBDAV_DIR || "tiyouju-packs"), error: msg };
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
    resp = await fetchWithRetry(doFetch, url, { method: "GET", headers: { Authorization: authHeader } }, { retries: 1 });
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
