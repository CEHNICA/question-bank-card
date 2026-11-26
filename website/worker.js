const GITHUB_REPO = "CEHNICA/question-bank-card";
const LATEST_RELEASE_PAGE = `https://github.com/${GITHUB_REPO}/releases/latest`;
const LATEST_MANIFEST_URL = `https://github.com/${GITHUB_REPO}/releases/latest/download/manifest.json`;
const CACHE_KEY_URL = "https://tiyouju.com/__cache/github-latest-release-v2";
const CACHE_TTL_SECONDS = 300;

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

function buildFromVersion(version, publishedAt = "", extraAssets = []) {
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
  return {
    tag,
    version: clean,
    name: `题有据 ${clean}`,
    published_at: publishedAt,
    release_url: `https://github.com/${GITHUB_REPO}/releases/tag/${tag}`,
    installer,
    portable,
    checksums,
    install_script: installScript,
  };
}

async function fetchLatestReleaseUncached() {
  // 1) 首选直接拉取随发布附带的 manifest.json（走 GitHub Releases CDN，不消耗 REST API 配额）。
  const manifestRes = await fetch(LATEST_MANIFEST_URL, {
    redirect: "follow",
    headers: { "User-Agent": "tiyouju-site" },
  });
  if (manifestRes.ok) {
    const manifest = await manifestRes.json();
    if (manifest && typeof manifest.version === "string" && manifest.version) {
      const publishedAt = manifestRes.headers.get("last-modified") || "";
      const assets = Array.isArray(manifest.assets) ? manifest.assets : [];
      return buildFromVersion(manifest.version, publishedAt, assets);
    }
  }

  // 2) 兜底：通过 releases/latest 的 302 跳转地址解析最新版本号（同样不走 API）。
  const headRes = await fetch(LATEST_RELEASE_PAGE, {
    method: "HEAD",
    redirect: "manual",
    headers: { "User-Agent": "tiyouju-site" },
  });
  const location = headRes.headers.get("location") || "";
  const match = /\/releases\/tag\/(v?[\d.]+)\s*$/.exec(location);
  if (match) {
    return buildFromVersion(match[1]);
  }

  throw new Error(`Unable to resolve latest release (manifest=${manifestRes.status}, head=${headRes.status})`);
}

async function getLatestRelease(ctx) {
  const cache = caches.default;
  const cacheKey = new Request(CACHE_KEY_URL);
  const cached = await cache.match(cacheKey);
  if (cached) {
    return cached.json();
  }

  const normalized = await fetchLatestReleaseUncached();
  if (!normalized.tag || !normalized.installer) {
    throw new Error("Incomplete release metadata");
  }

  const toCache = new Response(JSON.stringify(normalized), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": `public, max-age=${CACHE_TTL_SECONDS}`,
    },
  });
  ctx.waitUntil(cache.put(cacheKey, toCache.clone()));
  return normalized;
}

function jsonResponse(data, status = 200, maxAge = CACHE_TTL_SECONDS) {
  const headers = {
    "Content-Type": "application/json; charset=utf-8",
  };
  headers["Cache-Control"] = maxAge > 0 ? `public, max-age=${maxAge}` : "no-store";
  return new Response(JSON.stringify(data), { status, headers });
}

async function handleDownload(request, ctx, target) {
  let release;
  try {
    release = await getLatestRelease(ctx);
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
  };
  const entry = assetMap[target];
  if (!entry?.asset?.url) {
    return new Response("当前最新版本中未找到对应安装包文件。", {
      status: 404,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }

  const isHead = request.method === "HEAD";
  const upstreamHeaders = new Headers({ "User-Agent": "tiyouju-site" });
  const range = request.headers.get("Range");
  if (range) upstreamHeaders.set("Range", range);
  const ifRange = request.headers.get("If-Range");
  if (ifRange) upstreamHeaders.set("If-Range", ifRange);

  let upstream = await fetch(entry.asset.url, {
    method: isHead ? "HEAD" : "GET",
    redirect: "follow",
    headers: upstreamHeaders,
  });

  // GitHub 签名下载地址有时只签发了 GET，HEAD 返回 403/405 时改用已知文件元数据直接响应头。
  if (isHead && !upstream.ok && entry.asset.size > 0) {
    const fallbackHeaders = new Headers({
      "Content-Type": entry.contentType,
      "Content-Length": String(entry.asset.size),
      "Accept-Ranges": "bytes",
      "Content-Disposition": `${entry.disposition}; filename="${entry.asset.name}"`,
      "Cache-Control": `public, max-age=${CACHE_TTL_SECONDS}`,
    });
    return new Response(null, { status: 200, headers: fallbackHeaders });
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
  headers.set("Cache-Control", `public, max-age=${CACHE_TTL_SECONDS}`);

  return new Response(isHead ? null : upstream.body, {
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
        const release = await getLatestRelease(ctx);
        return jsonResponse({
          tag: release.tag,
          version: release.version,
          name: release.name,
          published_at: release.published_at,
          release_url: release.release_url,
          installer: release.installer
            ? { name: release.installer.name, size: release.installer.size, path: "/download/installer" }
            : null,
          portable: release.portable
            ? { name: release.portable.name, size: release.portable.size, path: "/download/portable" }
            : null,
          checksums: release.checksums
            ? { name: release.checksums.name, path: "/download/SHA256SUMS.txt" }
            : null,
        });
      } catch {
        return jsonResponse({ error: "暂时无法读取最新版本信息，请稍后重试。" }, 503, 0);
      }
    }

    if (request.method === "GET" || request.method === "HEAD") {
      if (pathname === "/download/installer") return handleDownload(request, ctx, "installer");
      if (pathname === "/download/portable") return handleDownload(request, ctx, "portable");
      if (pathname === "/download/SHA256SUMS.txt") return handleDownload(request, ctx, "checksums");
      if (pathname === "/download/install.ps1") return handleDownload(request, ctx, "install_script");
    }

    return env.ASSETS.fetch(request);
  },
};
