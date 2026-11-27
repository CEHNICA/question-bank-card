(function () {
  document.documentElement.classList.add("js-ready");

  // ---------- 移动端导航抽屉 ----------
  const menuBtn = document.getElementById("mobileMenuBtn");
  const mobileNav = document.getElementById("mobileNav");

  function setMenuOpen(open) {
    if (!menuBtn || !mobileNav) return;
    mobileNav.hidden = !open;
    menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
  }

  menuBtn?.addEventListener("click", (event) => {
    event.stopPropagation();
    const isOpen = menuBtn.getAttribute("aria-expanded") === "true";
    setMenuOpen(!isOpen);
  });

  mobileNav?.querySelectorAll("a").forEach((link) => {
    link.addEventListener("click", () => setMenuOpen(false));
  });

  document.addEventListener("click", (event) => {
    if (!mobileNav || mobileNav.hidden) return;
    if (!mobileNav.contains(event.target) && event.target !== menuBtn && !menuBtn?.contains(event.target)) {
      setMenuOpen(false);
    }
  });

  // ---------- 滚动渐显 ----------
  const revealEls = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window && revealEls.length) {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.08, rootMargin: "0px 0px -24px 0px" },
    );
    revealEls.forEach((el) => observer.observe(el));
    setTimeout(() => {
      revealEls.forEach((el) => el.classList.add("is-visible"));
    }, 1800);
  } else {
    revealEls.forEach((el) => el.classList.add("is-visible"));
  }

  // ---------- 特性卡片指针微光 ----------
  if (window.matchMedia("(hover: hover) and (pointer: fine)").matches) {
    document.querySelectorAll(".feature-card").forEach((card) => {
      card.addEventListener("pointermove", (event) => {
        const rect = card.getBoundingClientRect();
        card.style.setProperty("--mx", `${event.clientX - rect.left}px`);
        card.style.setProperty("--my", `${event.clientY - rect.top}px`);
      });
    });
  }

  // ---------- 界面图集切换 ----------
  const galleryImg = document.getElementById("galleryMainImg");
  const galleryCaption = document.getElementById("galleryCaption");
  const galleryZoomBtn = document.getElementById("galleryZoomBtn");
  const tabBtns = document.querySelectorAll(".tab-btn");
  const thumbBtns = document.querySelectorAll(".thumb-btn");

  function setGalleryItem(src, caption, altText) {
    if (!galleryImg || !galleryCaption || !galleryZoomBtn || !src) return;
    if (galleryImg.getAttribute("src") !== src) {
      galleryImg.style.opacity = "0.72";
      galleryImg.src = src;
      requestAnimationFrame(() => {
        galleryImg.style.opacity = "1";
      });
    }
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

  // ---------- 截图灯箱 ----------
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
    if (event.key === "Escape") {
      closeLightbox();
      setMenuOpen(false);
    }
  });

  async function writeClipboardText(text) {
    const value = String(text || "").trim();
    if (!value) return false;
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      const area = document.createElement("textarea");
      area.value = value;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(area);
      return ok;
    }
  }

  function flashCopiedButton(btn, fallbackText) {
    if (!btn) return;
    const prev = btn.dataset.prevLabel || btn.textContent;
    btn.dataset.prevLabel = prev;
    btn.textContent = "✓ 已复制";
    btn.classList.add("is-copied");
    clearTimeout(Number(btn.dataset.copyTimer || 0));
    const timer = setTimeout(() => {
      btn.textContent = prev || fallbackText || "一键复制";
      btn.classList.remove("is-copied");
    }, 1800);
    btn.dataset.copyTimer = String(timer);
  }

  // ---------- 一键复制 PowerShell 命令 ----------
  const copyBtn = document.getElementById("copyPsBtn");
  const psCmd = document.getElementById("psInstallCmd");
  copyBtn?.addEventListener("click", async () => {
    const text = psCmd?.textContent?.trim() || "";
    if (!text) return;
    if (await writeClipboardText(text)) {
      flashCopiedButton(copyBtn, "复制命令");
    }
  });

  // ---------- 3 步接入 AI 助手切换与一键复制 ----------
  const INTRO_PROMPT =
    "我的电脑已经安装了题有据。请先运行 tiyouju skill 读取当前版本的操作指南，接下来帮我查题库、核对试卷和总结 OneNote 笔记。";
  const MCP_SNIPPET = JSON.stringify(
    {
      mcpServers: {
        tiyouju: {
          command: "tiyouju",
          args: ["mcp"],
        },
      },
    },
    null,
    2,
  );
  const CONNECT_GUIDES = {
    doubao: {
      steps: [
        {
          title: "1. 下载或打开技能包 tiyouju-skill.zip",
          desc: "安装软件后已在本地自动生成压缩包，也可点右侧按钮直接下载。",
        },
        {
          title: "2. 拖进豆包电脑版「上传技能」",
          desc: "打开「技能·连接器·伙伴 → 我的技能 → 新建 → 上传技能」，把压缩包拖进去。",
        },
        {
          title: "3. 发一句开场指令就开始工作",
          desc: "复制下方开场指令发给豆包，即可自动查库、核对试卷与总结 OneNote 笔记。",
        },
      ],
      codeLabel: "给豆包的开场指令（点右侧一键复制）",
      codeBody: INTRO_PROMPT,
    },
    workbuddy: {
      steps: [
        {
          title: "1. 安装软件自动探测 ~/.workbuddy",
          desc: "安装题有据时会自动把 Skill 与 mcp.json 写入本机 WorkBuddy 目录。",
        },
        {
          title: "2. 在 WorkBuddy「连接器管理」点一次信任并启用",
          desc: "首次接入需手动点一次信任；如先装题有据后装 WorkBuddy，在设置里点「重新扫描」即可。",
        },
        {
          title: "3. 或直接粘贴通用 MCP 配置 / 导入技能包",
          desc: "右侧随时提供标准 tiyouju-skill.zip 下载，下方可直接一键复制 MCP JSON。",
        },
      ],
      codeLabel: "WorkBuddy 标准 MCP 配置 (~/.workbuddy/mcp.json)",
      codeBody: MCP_SNIPPET,
    },
    minimax: {
      steps: [
        {
          title: "1. 自动识别 ~/.minimax 与 MiniMax Agent",
          desc: "安装器会自动探测本机 MiniMax Agent 数据目录并同步 Skill 与 mcp.json。",
        },
        {
          title: "2. 桌面版可直接拖入 tiyouju-skill.zip",
          desc: "在 Expert Agents / 导入 Skill 中拖入右侧下载的压缩包，零代码即装即用。",
        },
        {
          title: "3. 也可粘贴标准 MCP JSON 开启工具直连",
          desc: "把下方配置粘进 MCP 设置并重启 MiniMax Agent，即可调用 30+ 题库与 OneNote 工具。",
        },
      ],
      codeLabel: "MiniMax Agent 标准 MCP 配置 (mcp.json)",
      codeBody: MCP_SNIPPET,
    },
    mcp: {
      steps: [
        {
          title: "1. 适用所有标准 MCP / CLI 客户端",
          desc: "覆盖 Trae 国际/国内版、Cursor、Claude、Codex、Qwen Code、Kimi、Cherry Studio、Chatbox 等。",
        },
        {
          title: "2. 点击右上角「一键复制」粘贴到 MCP 设置",
          desc: "题有据安装后已把 tiyouju.exe 写入用户 PATH，标准配置可直接复用，不依赖 Node/Python。",
        },
        {
          title: "3. 或用 Codex 一行命令行接入",
          desc: '执行：codex mcp add tiyouju -- "tiyouju" mcp ，即可立即调用题库工具。',
        },
      ],
      codeLabel: "通用 stdio MCP 配置（点右上角一键复制）",
      codeBody: MCP_SNIPPET,
    },
  };

  const connectTabs = document.querySelectorAll("[data-connect-tab]");
  const connectStepsEl = document.getElementById("webConnectSteps");
  const connectCodeLabelEl = document.getElementById("webConnectCodeLabel");
  const connectCodeBodyEl = document.getElementById("webConnectCodeBody");
  const connectCopyCodeBtn = document.getElementById("webConnectCopyCode");
  const connectCopyIntroBtn = document.getElementById("webConnectCopyIntro");

  function renderConnectTab(tabId) {
    const guide = CONNECT_GUIDES[tabId];
    if (!guide) return;
    connectTabs.forEach((btn) => {
      const active = btn.dataset.connectTab === tabId;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-selected", active ? "true" : "false");
    });
    if (connectStepsEl) {
      connectStepsEl.textContent = "";
      guide.steps.forEach((step) => {
        const li = document.createElement("li");
        const strong = document.createElement("strong");
        strong.textContent = step.title;
        const span = document.createElement("span");
        span.textContent = step.desc;
        li.appendChild(strong);
        li.appendChild(span);
        connectStepsEl.appendChild(li);
      });
    }
    if (connectCodeLabelEl) connectCodeLabelEl.textContent = guide.codeLabel;
    if (connectCodeBodyEl) connectCodeBodyEl.textContent = guide.codeBody;
  }

  connectTabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      renderConnectTab(btn.dataset.connectTab || "doubao");
    });
  });

  connectCopyCodeBtn?.addEventListener("click", async () => {
    const text = connectCodeBodyEl?.textContent || "";
    if (await writeClipboardText(text)) {
      flashCopiedButton(connectCopyCodeBtn, "一键复制");
    }
  });

  connectCopyIntroBtn?.addEventListener("click", async () => {
    if (await writeClipboardText(INTRO_PROMPT)) {
      flashCopiedButton(connectCopyIntroBtn, "一键复制 AI 开场指令");
    }
  });

  document.querySelectorAll("[data-copy-prompt]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const prompt = btn.getAttribute("data-copy-prompt") || "";
      if (await writeClipboardText(prompt)) {
        flashCopiedButton(btn, btn.textContent);
      }
    });
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
        const label = installerMb
          ? `下载 Windows 安装版 (${release.tag} · ${installerMb})`
          : `下载 Windows 安装版 (${release.tag})`;
        const span = heroInstallerBtn.querySelector("span");
        if (span) span.textContent = label;
        else heroInstallerBtn.textContent = label;
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
