const GITHUB_REPO = "CEHNICA/question-bank-card";
const LATEST_RELEASE_PAGE = `https://github.com/${GITHUB_REPO}/releases/latest`;
const LATEST_MANIFEST_URL = `https://github.com/${GITHUB_REPO}/releases/latest/download/manifest.json`;
const CACHE_KEY_URL = "https://tiyouju.com/__cache/github-latest-release-v4";
const REFRESH_GUARD_URL = "https://tiyouju.com/__cache/github-refresh-cooldown-v1";
const R2_LATEST_KEY = "latest-release.json";
const R2_RELEASES_PREFIX = "releases/";
const EDGE_CACHE_TTL_SECONDS = 60;
const R2_FRESH_MS = 120 * 1000;
const BINARY_CACHE_TTL_SECONDS = 3600;
const RELEASE_NOTES_LIMIT = 4000;
const KEEP_R2_RELEASES = 3;

function extractHighlights(markdown) {
  if (!markdown) return [];
  const items = [];
  let inHighlights = false;
  for (const rawLine of String(markdown).split(/\r?\n/)) {
    const line = rawLine.trim();
    if (/^###\s+/.test(line)) {
      inHighlights = !/验证范围/.test(line);
      continue;
    }
    if (!inHighlights && items.length === 0 && !/^(?:\d+\.|[-*])\s+/.test(line)) {
      continue;
    }
    const bullet = /^(?:\d+\.|[-*])\s+(.+)$/.exec(line);
    if (!bullet) continue;
    const cleaned = bullet[1]
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .replace(/[`*]/g, "")
      .replace(/\s+/g, " ")
      .trim();
    if (!cleaned || cleaned.length < 4) continue;
    items.push(cleaned.length > 110 ? `${cleaned.slice(0, 108)}…` : cleaned);
    if (items.length >= 4) break;
  }
  return items;
}

async function fetchReleaseNotes(version) {
  const clean = String(version || "").replace(/^v/, "").trim();
  if (!clean) return "";
  const url = `https://github.com/${GITHUB_REPO}/releases/download/v${clean}/release-notes-v${clean}.md`;
  try {
    const res = await fetch(url, {
      redirect: "follow",
      headers: { "User-Agent": "tiyouju-site" },
      signal: AbortSignal.timeout(2500),
    });
    if (!res.ok) return "";
    const text = await res.text();
    return text.trim().slice(0, RELEASE_NOTES_LIMIT);
  } catch {
    return "";
  }
}

function pickAsset(assets, matcher, version) {
  const item = assets.find((asset) => asset && typeof asset.name === "string" && matcher(asset.name));
  if (!item) return null;
  const tag = version ? `v${version}` : "";
  const url =
    item.browser_download_url ||
    (tag
      ? `https://github.com/${GITHUB_REPO}/releases/download/${tag}/${item.name}`
      : `https://github.com/${GITHUB_REPO}/releases/latest/download/${item.name}`);
  return {
    name: item.name,
    size: Number(item.size || 0),
    url,
  };
}

function buildFromVersion(version, publishedAt = "", extraAssets = [], notes = "") {
  const clean = String(version || "").replace(/^v/, "");
  const tag = `v${clean}`;
  const defaults = [
    { name: `TiYouJu-Setup-${clean}.exe`, size: 0 },
    { name: `QuestionBankCard-${clean}-portable.zip`, size: 0 },
    { name: "SHA256SUMS.txt", size: 0 },
    { name: "install.ps1", size: 0 },
  ];
  const merged = extraAssets.length ? extraAssets : defaults;
  const installer = pickAsset(merged, (name) => /^TiYouJu-Setup-.*\.exe$/i.test(name), clean);
  const portable = pickAsset(merged, (name) => /^QuestionBankCard-.*-portable\.zip$/i.test(name), clean);
  const checksums =
    pickAsset(merged, (name) => name === "SHA256SUMS.txt", clean) ||
    pickAsset([{ name: "SHA256SUMS.txt", size: 0 }], () => true, clean);
  const installScript =
    pickAsset(merged, (name) => name === "install.ps1", clean) ||
    pickAsset([{ name: "install.ps1", size: 0 }], () => true, clean);
  const skillPack = pickAsset(merged, (name) => name === "tiyouju-skill.zip", clean);
  const cleanNotes = String(notes || "").trim().slice(0, RELEASE_NOTES_LIMIT);
  return {
    tag,
    version: clean,
    name: `题有据 ${clean}`,
    published_at: publishedAt,
    synced_at: new Date().toISOString(),
    release_url: `https://github.com/${GITHUB_REPO}/releases/tag/${tag}`,
    notes: cleanNotes,
    highlights: extractHighlights(cleanNotes),
    installer,
    portable,
    checksums,
    install_script: installScript,
    skill_pack: skillPack,
  };
}

async function fetchLatestReleaseUncached() {
  // 1) 拉取 GitHub Releases CDN 托管的 manifest.json（带超时，避免冷回源阻塞过久）。
  const manifestRes = await fetch(LATEST_MANIFEST_URL, {
    redirect: "follow",
    headers: { "User-Agent": "tiyouju-site" },
    signal: AbortSignal.timeout(6000),
  });
  if (manifestRes.ok) {
    const manifest = await manifestRes.json();
    if (manifest && typeof manifest.version === "string" && manifest.version) {
      const publishedAt = manifestRes.headers.get("last-modified") || "";
      const assets = Array.isArray(manifest.assets) ? manifest.assets : [];
      const notes = await fetchReleaseNotes(manifest.version);
      return buildFromVersion(manifest.version, publishedAt, assets, notes);
    }
  }

  // 2) 兜底：通过 releases/latest 的 302 跳转解析最新版本号。
  const headRes = await fetch(LATEST_RELEASE_PAGE, {
    method: "HEAD",
    redirect: "manual",
    headers: { "User-Agent": "tiyouju-site" },
    signal: AbortSignal.timeout(4000),
  });
  const location = headRes.headers.get("location") || "";
  const match = /\/releases\/tag\/(v?[\d.]+)\s*$/.exec(location);
  if (match) {
    const notes = await fetchReleaseNotes(match[1]);
    return buildFromVersion(match[1], "", [], notes);
  }

  throw new Error(`Unable to resolve latest release (manifest=${manifestRes.status}, head=${headRes.status})`);
}

function r2AssetKey(version, assetName) {
  const clean = String(version || "").replace(/^v/, "").trim();
  return `${R2_RELEASES_PREFIX}v${clean}/${assetName}`;
}

async function putStreamToR2(bucket, key, stream, knownLength, contentType) {
  if (!bucket || !stream) return false;
  if (Number.isFinite(knownLength) && knownLength > 0 && typeof FixedLengthStream === "function") {
    const { readable, writable } = new FixedLengthStream(knownLength);
    await Promise.all([
      stream.pipeTo(writable),
      bucket.put(key, readable, { httpMetadata: { contentType } }),
    ]);
    return true;
  }
  const res = new Response(stream);
  const buf = await res.arrayBuffer();
  await bucket.put(key, buf, { httpMetadata: { contentType } });
  return true;
}

async function mirrorAssetToR2(env, version, asset, contentType) {
  if (!env?.RELEASES || !version || !asset?.name || !asset?.url) return false;
  const key = r2AssetKey(version, asset.name);
  try {
    const existing = await env.RELEASES.head(key);
    if (existing && (asset.size <= 0 || existing.size === asset.size)) {
      return true;
    }
    const res = await fetch(asset.url, {
      redirect: "follow",
      headers: { "User-Agent": "tiyouju-site" },
      signal: AbortSignal.timeout(45000),
    });
    if (!res.ok || !res.body) return false;
    const headerLen = Number(res.headers.get("content-length") || 0);
    const knownLen = headerLen > 0 ? headerLen : Number(asset.size || 0);
    return await putStreamToR2(env.RELEASES, key, res.body, knownLen, contentType);
  } catch {
    return false;
  }
}

async function pruneOldR2Releases(env, activeTag) {
  if (!env?.RELEASES) return;
  try {
    const listed = await env.RELEASES.list({ prefix: R2_RELEASES_PREFIX, delimiter: "/" });
    const prefixes = (listed.delimitedPrefixes || []).filter(Boolean);
    const activePrefix = `${R2_RELEASES_PREFIX}${activeTag}/`;
    const candidates = prefixes.filter((p) => p !== activePrefix);
    // 按版本号自然降序保留最新 KEEP_R2_RELEASES 个目录，避免 R2 堆积旧安装包
    candidates.sort((a, b) => b.localeCompare(a, undefined, { numeric: true }));
    const stale = candidates.slice(Math.max(0, KEEP_R2_RELEASES - 1));
    for (const prefix of stale) {
      const objs = await env.RELEASES.list({ prefix });
      const keys = (objs.objects || []).map((o) => o.key);
      if (keys.length) await env.RELEASES.delete(keys);
    }
  } catch {
    // 清理失败不影响正常发布与下载
  }
}

async function mirrorReleaseToR2(env, release) {
  if (!env?.RELEASES || !release?.version) return;
  const jobs = [
    [release.checksums, "text/plain; charset=utf-8"],
    [release.install_script, "text/plain; charset=utf-8"],
    [release.skill_pack, "application/zip"],
    [release.installer, "application/octet-stream"],
    [release.portable, "application/octet-stream"],
  ];
  await Promise.allSettled(
    jobs.filter(([asset]) => asset?.name && asset?.url).map(([asset, ct]) => mirrorAssetToR2(env, release.version, asset, ct)),
  );
  await pruneOldR2Releases(env, release.tag);
}

async function saveReleaseCaches(ctx, env, release) {
  const payload = JSON.stringify(release);
  const edgeResp = new Response(payload, {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": `public, max-age=${EDGE_CACHE_TTL_SECONDS}`,
    },
  });
  const tasks = [caches.default.put(new Request(CACHE_KEY_URL), edgeResp)];
  if (env?.RELEASES) {
    tasks.push(
      env.RELEASES.put(R2_LATEST_KEY, payload, {
        httpMetadata: { contentType: "application/json; charset=utf-8" },
      }),
    );
  }
  const promise = Promise.allSettled(tasks);
  if (ctx?.waitUntil) ctx.waitUntil(promise);
  else await promise;
}

async function refreshReleaseNow(ctx, env) {
  const normalized = await fetchLatestReleaseUncached();
  if (!normalized.tag || !normalized.installer) {
    throw new Error("Incomplete release metadata");
  }
  await saveReleaseCaches(ctx, env, normalized);
  const mirrorJob = mirrorReleaseToR2(env, normalized);
  if (ctx?.waitUntil) ctx.waitUntil(mirrorJob);
  else await mirrorJob;
  return normalized;
}

async function getLatestRelease(ctx, env, { forceRefresh = false } = {}) {
  const cache = caches.default;
  const cacheKey = new Request(CACHE_KEY_URL);

  if (forceRefresh) {
    const guardKey = new Request(REFRESH_GUARD_URL);
    const recentlyRefreshed = await cache.match(guardKey);
    if (!recentlyRefreshed) {
      const marker = new Response("1", { headers: { "Cache-Control": "public, max-age=15" } });
      if (ctx?.waitUntil) ctx.waitUntil(cache.put(guardKey, marker));
      return refreshReleaseNow(ctx, env);
    }
  }

  // 1) 本机 Edge PoP 内存缓存（~1ms）
  const edgeCached = await cache.match(cacheKey);
  if (edgeCached) {
    return { release: await edgeCached.json(), source: "edge" };
  }

  // 2) Cloudflare R2 全局共享缓存（所有 PoP ~15ms 共享最新清单 + Stale-While-Revalidate）
  if (env?.RELEASES) {
    try {
      const r2Obj = await env.RELEASES.get(R2_LATEST_KEY);
      if (r2Obj) {
        const data = await r2Obj.json();
        if (data?.tag && data?.installer) {
          const uploadedMs = r2Obj.uploaded ? r2Obj.uploaded.getTime() : 0;
          const isStale = !uploadedMs || Date.now() - uploadedMs > R2_FRESH_MS;
          const edgeResp = new Response(JSON.stringify(data), {
            headers: {
              "Content-Type": "application/json; charset=utf-8",
              "Cache-Control": `public, max-age=${EDGE_CACHE_TTL_SECONDS}`,
            },
          });
          if (ctx?.waitUntil) {
            ctx.waitUntil(cache.put(cacheKey, edgeResp));
            if (isStale) {
              ctx.waitUntil(refreshReleaseNow(ctx, env).catch(() => {}));
            }
          }
          return { release: data, source: isStale ? "r2-swr" : "r2" };
        }
      }
    } catch {
      // 回退到实时拉取 GitHub
    }
  }

  // 3) 冷启动兜底：回源 GitHub 并顺手写入 R2 + 镜像二进制包
  const release = await refreshReleaseNow(ctx, env);
  return { release, source: "origin" };
}

function jsonResponse(data, status = 200, maxAge = EDGE_CACHE_TTL_SECONDS, cacheTag = "") {
  const headers = {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": maxAge > 0 ? `public, max-age=${maxAge}` : "no-store",
  };
  if (cacheTag) headers["X-TiYouJu-Cache"] = cacheTag;
  return new Response(JSON.stringify(data), { status, headers });
}

function parseByteRange(rangeHeader, totalSize) {
  if (!rangeHeader || !Number.isFinite(totalSize) || totalSize <= 0) return null;
  const m = /^bytes=(\d*)-(\d*)$/i.exec(rangeHeader.trim());
  if (!m) return null;
  if (!m[1] && !m[2]) return null;
  let start;
  let end;
  if (!m[1]) {
    const suffix = Number(m[2]);
    if (!Number.isFinite(suffix) || suffix <= 0) return null;
    start = Math.max(0, totalSize - suffix);
    end = totalSize - 1;
  } else {
    start = Number(m[1]);
    end = m[2] ? Math.min(totalSize - 1, Number(m[2])) : totalSize - 1;
  }
  if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || start > end || start >= totalSize) {
    return { invalid: true };
  }
  return { offset: start, length: end - start + 1, start, end };
}

async function handleDownload(request, env, ctx, target) {
  let release;
  try {
    ({ release } = await getLatestRelease(ctx, env));
  } catch {
    return new Response("暂时无法获取最新安装包，请稍后再试或前往 GitHub Releases 下载。", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }

  const assetMap = {
    installer: { asset: release.installer, disposition: "attachment", contentType: "application/octet-stream" },
    portable: { asset: release.portable, disposition: "attachment", contentType: "application/octet-stream" },
    checksums: { asset: release.checksums, disposition: "inline", contentType: "text/plain; charset=utf-8" },
    install_script: { asset: release.install_script, disposition: "inline", contentType: "text/plain; charset=utf-8" },
    skill_pack: { asset: release.skill_pack, disposition: "attachment", contentType: "application/zip" },
  };
  const entry = assetMap[target];
  if (!entry?.asset?.url) {
    return new Response("当前最新版本中未找到对应安装包文件。", {
      status: 404,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }

  const isHead = request.method === "HEAD";
  const rangeHeader = request.headers.get("Range");
  const r2Key = r2AssetKey(release.version, entry.asset.name);

  // 1) R2 边缘直吐（无回源延迟、全速带宽 + 原生 Range 断点续传/多线程分片）
  if (env?.RELEASES) {
    try {
      const headers = new Headers({
        "Content-Type": entry.contentType,
        "Accept-Ranges": "bytes",
        "Content-Disposition": `${entry.disposition}; filename="${entry.asset.name}"`,
        "Cache-Control": `public, max-age=${BINARY_CACHE_TTL_SECONDS}`,
        "X-TiYouJu-Cache": "r2-hit",
      });

      if (isHead) {
        const meta = await env.RELEASES.head(r2Key);
        if (meta && meta.size > 0) {
          if (meta.httpEtag) headers.set("ETag", meta.httpEtag);
          headers.set("Content-Length", String(meta.size));
          return new Response(null, { status: 200, headers });
        }
      } else if (!rangeHeader) {
        const edgeCache = typeof caches !== "undefined" ? caches.default : null;
        const edgeCacheReq = edgeCache
          ? new Request(`https://tiyouju.com/__r2_edge/${encodeURIComponent(r2Key)}`)
          : null;
        if (edgeCache && edgeCacheReq) {
          const hit = await edgeCache.match(edgeCacheReq).catch(() => null);
          if (hit) {
            const hitHeaders = new Headers(hit.headers);
            hitHeaders.set("X-TiYouJu-Cache", "edge-hit");
            return new Response(hit.body, { status: 200, headers: hitHeaders });
          }
        }
        const r2Obj = await env.RELEASES.get(r2Key);
        if (r2Obj && r2Obj.size > 0) {
          if (r2Obj.httpEtag) headers.set("ETag", r2Obj.httpEtag);
          headers.set("Content-Length", String(r2Obj.size));
          const resp = new Response(r2Obj.body, { status: 200, headers });
          if (edgeCache && edgeCacheReq && ctx?.waitUntil) {
            ctx.waitUntil(edgeCache.put(edgeCacheReq, resp.clone()).catch(() => {}));
          }
          return resp;
        }
      } else {
        let totalSize = Number(entry.asset.size) || 0;
        let parsedRange = totalSize > 0 ? parseByteRange(rangeHeader, totalSize) : null;
        if (!parsedRange) {
          const meta = await env.RELEASES.head(r2Key);
          if (meta && meta.size > 0) {
            totalSize = meta.size;
            parsedRange = parseByteRange(rangeHeader, totalSize);
          }
        }
        if (parsedRange?.invalid) {
          return new Response(null, {
            status: 416,
            headers: { "Content-Range": `bytes */${totalSize}` },
          });
        }
        if (parsedRange) {
          const r2Obj = await env.RELEASES.get(r2Key, {
            range: { offset: parsedRange.offset, length: parsedRange.length },
          });
          if (r2Obj && r2Obj.size > 0) {
            const actualTotal = r2Obj.size;
            if (parsedRange.start >= actualTotal) {
              return new Response(null, {
                status: 416,
                headers: { "Content-Range": `bytes */${actualTotal}` },
              });
            }
            const actualEnd = Math.min(parsedRange.end, actualTotal - 1);
            const actualLen = actualEnd - parsedRange.start + 1;
            if (r2Obj.httpEtag) headers.set("ETag", r2Obj.httpEtag);
            headers.set("Content-Length", String(actualLen));
            headers.set("Content-Range", `bytes ${parsedRange.start}-${actualEnd}/${actualTotal}`);
            return new Response(r2Obj.body, { status: 206, headers });
          }
        }
      }
    } catch {
      // R2 读异常时无缝回退到 GitHub 上游
    }
  }

  // 2) R2 尚未缓存时的首次回源 + 边下边写回 R2
  const upstreamHeaders = new Headers({ "User-Agent": "tiyouju-site" });
  if (rangeHeader) upstreamHeaders.set("Range", rangeHeader);
  const ifRange = request.headers.get("If-Range");
  if (ifRange) upstreamHeaders.set("If-Range", ifRange);

  const upstream = await fetch(entry.asset.url, {
    method: isHead ? "HEAD" : "GET",
    redirect: "follow",
    headers: upstreamHeaders,
  });

  if (isHead && !upstream.ok && entry.asset.size > 0) {
    return new Response(null, {
      status: 200,
      headers: {
        "Content-Type": entry.contentType,
        "Content-Length": String(entry.asset.size),
        "Accept-Ranges": "bytes",
        "Content-Disposition": `${entry.disposition}; filename="${entry.asset.name}"`,
        "Cache-Control": `public, max-age=${EDGE_CACHE_TTL_SECONDS}`,
        "X-TiYouJu-Cache": "meta-head",
      },
    });
  }

  if (!upstream.ok && upstream.status !== 206) {
    return new Response(`下载源暂时不可用（HTTP ${upstream.status}），请稍后重试。`, {
      status: 502,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }

  const headers = new Headers();
  for (const name of ["Content-Length", "Content-Range", "Accept-Ranges", "ETag"]) {
    const val = upstream.headers.get(name);
    if (val) headers.set(name, val);
  }
  if (!headers.has("Content-Length") && entry.asset.size > 0 && upstream.status === 200) {
    headers.set("Content-Length", String(entry.asset.size));
  }
  headers.set("Content-Type", entry.contentType);
  headers.set("Content-Disposition", `${entry.disposition}; filename="${entry.asset.name}"`);
  headers.set("Cache-Control", `public, max-age=${BINARY_CACHE_TTL_SECONDS}`);
  headers.set("X-TiYouJu-Cache", "origin-fill");

  if (isHead || !upstream.body) {
    return new Response(null, { status: upstream.status, headers });
  }

  // 完整 200 响应时顺手用 tee() 异步落入 R2，下一次全球下载直接命中 R2
  if (env?.RELEASES && upstream.status === 200 && !rangeHeader) {
    const [clientStream, r2Stream] = upstream.body.tee();
    const knownLen = Number(headers.get("Content-Length") || entry.asset.size || 0);
    if (ctx?.waitUntil) {
      ctx.waitUntil(putStreamToR2(env.RELEASES, r2Key, r2Stream, knownLen, entry.contentType).catch(() => {}));
    }
    return new Response(clientStream, { status: 200, headers });
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers,
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const { pathname } = url;

    if (pathname === "/api/latest-release" && request.method === "GET") {
      try {
        const forceRefresh = url.searchParams.get("refresh") === "1";
        const result = await getLatestRelease(ctx, env, { forceRefresh });
        const release = result.release || result;
        return jsonResponse(
          {
            tag: release.tag,
            version: release.version,
            name: release.name,
            published_at: release.published_at,
            synced_at: release.synced_at || "",
            release_url: release.release_url,
            notes: release.notes || "",
            highlights: Array.isArray(release.highlights) ? release.highlights : [],
            installer: release.installer
              ? { name: release.installer.name, size: release.installer.size, path: "/download/installer" }
              : null,
            portable: release.portable
              ? { name: release.portable.name, size: release.portable.size, path: "/download/portable" }
              : null,
            checksums: release.checksums
              ? { name: release.checksums.name, path: "/download/SHA256SUMS.txt" }
              : null,
            skill_pack: release.skill_pack
              ? { name: release.skill_pack.name, size: release.skill_pack.size, path: "/download/skill" }
              : null,
          },
          200,
          forceRefresh ? 0 : EDGE_CACHE_TTL_SECONDS,
          result.source || "",
        );
      } catch {
        return jsonResponse({ error: "暂时无法读取最新版本信息，请稍后重试。" }, 503, 0);
      }
    }

    if (request.method === "GET" || request.method === "HEAD") {
      if (pathname === "/download/installer") return handleDownload(request, env, ctx, "installer");
      if (pathname === "/download/portable") return handleDownload(request, env, ctx, "portable");
      if (pathname === "/download/SHA256SUMS.txt") return handleDownload(request, env, ctx, "checksums");
      if (pathname === "/download/install.ps1") return handleDownload(request, env, ctx, "install_script");
      if (pathname === "/download/skill") return handleDownload(request, env, ctx, "skill_pack");
    }

    return env.ASSETS.fetch(request);
  },

  async scheduled(_event, env, ctx) {
    // 每 5 分钟后台预同步最新 GitHub Release 与二进制包到 R2，用户始终零等待
    ctx.waitUntil(refreshReleaseNow(ctx, env).catch(() => {}));
  },
};
