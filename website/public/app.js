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

  // 在能访问 GitHub API 的网络下自动解析最新安装包和便携版直链；不可达时静默回退到 Releases 页。
  fetch("https://api.github.com/repos/CEHNICA/question-bank-card/releases/latest", {
    headers: { Accept: "application/vnd.github+json" },
  })
    .then((res) => (res.ok ? res.json() : null))
    .then((release) => {
      if (!release || !Array.isArray(release.assets)) return;
      if (typeof release.tag_name === "string" && release.tag_name) {
        const badge = document.getElementById("versionBadge");
        if (badge) badge.textContent = release.tag_name;
      }
      const installer = release.assets.find((a) => /^TiYouJu-Setup-.*\.exe$/i.test(a.name));
      const portable = release.assets.find((a) => /^QuestionBankCard-.*-portable\.zip$/i.test(a.name));
      if (installer?.browser_download_url) {
        ["downloadInstallerBtn", "downloadInstallerCardBtn"].forEach((id) => {
          const el = document.getElementById(id);
          if (el) el.href = installer.browser_download_url;
        });
      }
      if (portable?.browser_download_url) {
        ["downloadPortableBtn", "downloadPortableCardBtn"].forEach((id) => {
          const el = document.getElementById(id);
          if (el) el.href = portable.browser_download_url;
        });
      }
    })
    .catch(() => {});
})();
