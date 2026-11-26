(function () {
  const galleryImg = document.getElementById("galleryMainImg");
  const galleryCaption = document.getElementById("galleryCaption");
  const galleryZoomBtn = document.getElementById("galleryZoomBtn");
  const tabBtns = document.querySelectorAll(".tab-btn");
  const thumbBtns = document.querySelectorAll(".thumb-btn");

  function setGalleryItem(src, caption, altText) {
    if (!galleryImg || !galleryCaption || !galleryZoomBtn) return;
    galleryImg.src = src;
    galleryImg.alt = altText || "题有据界面截图";
    galleryCaption.textContent = caption;
    galleryZoomBtn.dataset.zoomSrc = src;
    galleryZoomBtn.dataset.zoomCaption = caption;

    tabBtns.forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.src === src);
    });
    thumbBtns.forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.src === src);
    });
  }

  tabBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      setGalleryItem(btn.dataset.src || "", btn.dataset.caption || "", btn.textContent?.trim());
    });
  });

  thumbBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      setGalleryItem(btn.dataset.src || "", btn.dataset.caption || "", btn.textContent?.trim());
    });
  });

  const lightbox = document.getElementById("lightbox");
  const lightboxImg = document.getElementById("lightboxImg");
  const lightboxCaption = document.getElementById("lightboxCaption");
  const lightboxClose = document.getElementById("lightboxClose");

  function openLightbox(src, caption) {
    if (!lightbox || !lightboxImg || !lightboxCaption || !src) return;
    lightboxImg.src = src;
    lightboxImg.alt = caption || "界面截图预览";
    lightboxCaption.textContent = caption || "";
    lightbox.classList.add("is-open");
  }

  function closeLightbox() {
    if (!lightbox) return;
    lightbox.classList.remove("is-open");
  }

  document.querySelectorAll("[data-zoom-src]").forEach((el) => {
    el.addEventListener("click", () => {
      openLightbox(el.dataset.zoomSrc, el.dataset.zoomCaption);
    });
  });

  lightboxClose?.addEventListener("click", closeLightbox);
  lightbox?.addEventListener("click", (event) => {
    if (event.target === lightbox) closeLightbox();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeLightbox();
  });

  const copyBtn = document.getElementById("copyPsBtn");
  const psCmd = document.getElementById("psInstallCmd");
  copyBtn?.addEventListener("click", async () => {
    const text = psCmd?.textContent?.trim() || "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      document.body.removeChild(area);
    }
    const prev = copyBtn.textContent;
    copyBtn.textContent = "已复制";
    setTimeout(() => {
      copyBtn.textContent = prev || "复制命令";
    }, 1800);
  });

  function formatMb(bytes) {
    if (!Number.isFinite(bytes) || bytes <= 0) return "";
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatDate(iso) {
    if (typeof iso !== "string" || !iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }

  // 从同源 /api/latest-release 拉取 GitHub 最新正式版信息（国内免代理可用）；按钮链接始终直指 /download/* 在当前页下载。
  fetch("/api/latest-release")
    .then((res) => (res.ok ? res.json() : null))
    .then((release) => {
      if (!release || !release.tag) return;
      const badge = document.getElementById("versionBadge");
      if (badge) badge.textContent = release.tag;

      const installerMb = formatMb(release.installer?.size);
      const portableMb = formatMb(release.portable?.size);
      const dateText = formatDate(release.published_at);

      const heroInstallerBtn = document.getElementById("downloadInstallerBtn");
      if (heroInstallerBtn) {
        heroInstallerBtn.textContent = installerMb
          ? `下载 Windows 安装版 (${release.tag} · ${installerMb})`
          : `下载 Windows 安装版 (${release.tag})`;
      }

      const cardInstallerBtn = document.getElementById("downloadInstallerCardBtn");
      if (cardInstallerBtn && release.installer?.name) {
        cardInstallerBtn.textContent = installerMb
          ? `下载 ${release.installer.name} (${installerMb})`
          : `下载 ${release.installer.name}`;
      }

      const heroPortableBtn = document.getElementById("downloadPortableBtn");
      if (heroPortableBtn && portableMb) {
        heroPortableBtn.textContent = `免安装便携版 (${portableMb})`;
      }

      const cardPortableBtn = document.getElementById("downloadPortableCardBtn");
      if (cardPortableBtn && release.portable?.name) {
        cardPortableBtn.textContent = portableMb
          ? `下载便携版 (${portableMb})`
          : `下载 ${release.portable.name}`;
      }

      const meta = document.getElementById("releaseMeta");
      if (meta) {
        const parts = [`当前最新：<strong>${release.tag}</strong>`];
        if (dateText) parts.push(`更新于 ${dateText}`);
        parts.push("点按钮直接下载，自动同步 GitHub 最新正式版");
        meta.innerHTML = parts.join(" · ");
      }

      const cardMeta = document.getElementById("downloadCardMeta");
      if (cardMeta) {
        const sizeNote = installerMb ? `安装包 ${installerMb}，` : "";
        cardMeta.textContent = `当前已同步 ${release.tag}（${sizeNote}与 GitHub Releases 同步），点击即在当前网页直接下载。`;
      }
    })
    .catch(() => {});
})();
