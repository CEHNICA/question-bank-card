(function (global) {
  "use strict";

  const CLIENT_NAMES = {
    universal_agents: "通用 Agent 目录 (~/.agents)",
    trae_cn: "Trae 国内版",
    trae: "Trae 国际版",
    workbuddy: "腾讯 WorkBuddy",
    codebuddy: "CodeBuddy",
    codebuddy_dot: "CodeBuddy (.mcp.json)",
    minimax: "MiniMax",
    minimax_agent: "MiniMax Agent 桌面版",
    minimax_data_dir: "MiniMax 数据目录",
    cursor: "Cursor",
    claude_code: "Claude Code",
    claude_desktop: "Claude Desktop",
    codex: "Codex",
    antigravity: "Antigravity",
    windsurf: "Windsurf",
    qwen_code: "Qwen Code",
    qoder: "Qoder",
    lingma: "通义灵码",
    kimi: "Kimi CLI",
    iflow: "iFlow",
    openclaw: "OpenClaw",
    cline: "Cline",
    roo_code: "Roo Code",
    kilocode: "Kilo Code",
  };

  const TABS = [
    { id: "doubao", label: "豆包电脑版" },
    { id: "workbuddy_minimax", label: "WorkBuddy / MiniMax" },
    { id: "mcp", label: "Cursor / Trae / Cherry / Codex" },
  ];

  let mountedHost = null;
  let activeTab = "doubao";
  let currentData = null;

  async function requestJson(url, options = {}) {
    const headers = Object.assign({ "X-QB-Request": "1" }, options.headers || {});
    if (options.body && typeof options.body !== "string") {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(options.body);
    }
    const resp = await fetch(url, Object.assign({ cache: "no-store" }, options, { headers }));
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data.error || `请求失败 (${resp.status})`);
    }
    return data;
  }

  async function copyText(text, button, defaultLabel) {
    const value = String(text || "");
    if (!value) return;
    let copied = false;
    if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
      try {
        await navigator.clipboard.writeText(value);
        copied = true;
      } catch {}
    }
    if (!copied) {
      const area = document.createElement("textarea");
      area.value = value;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        copied = document.execCommand("copy");
      } catch {}
      document.body.removeChild(area);
    }
    if (button && copied) {
      const prev = defaultLabel || button.textContent;
      button.textContent = "✓ 已复制";
      button.classList.add("is-copied");
      window.setTimeout(() => {
        button.textContent = prev;
        button.classList.remove("is-copied");
      }, 1700);
    }
  }

  function makeButton(label, className, onClick) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = className;
    btn.textContent = label;
    btn.addEventListener("click", onClick);
    return btn;
  }

  function clientLabel(id) {
    return CLIENT_NAMES[id] || String(id || "");
  }

  function renderBadges(data, statusRow) {
    statusRow.textContent = "";
    const addBadge = (text, ok) => {
      const badge = document.createElement("span");
      badge.className = `ai-connect-badge ${ok ? "is-ok" : "is-muted"}`;
      badge.textContent = text;
      statusRow.appendChild(badge);
    };
    addBadge(data.cli_ready ? "命令行 tiyouju.exe 已就绪" : "源码开发模式 (使用 PATH / CLI)", Boolean(data.cli_ready));
    addBadge(data.skill_zip_ready ? "技能包 tiyouju-skill.zip 已备好" : "点击下方按钮即可生成技能包", Boolean(data.skill_zip_ready));

    const configured = (data.clients || [])
      .filter((item) => item.status === "configured")
      .map((item) => clientLabel(item.client));
    const agents = (data.detected_agents || [])
      .filter((id) => id !== "universal_agents")
      .map((id) => clientLabel(id));
    const merged = Array.from(new Set(configured.concat(agents)));
    if (merged.length) {
      addBadge(`已探测：${merged.slice(0, 4).join("、")}${merged.length > 4 ? " 等" : ""}`, true);
    } else {
      addBadge("通用模式：拖入技能包或复制配置即用", false);
    }
  }

  function buildStepsList(steps) {
    const list = document.createElement("ol");
    list.className = "ai-connect-steps";
    steps.forEach((step) => {
      const li = document.createElement("li");
      const strong = document.createElement("strong");
      strong.textContent = step.title;
      const span = document.createElement("span");
      span.textContent = step.desc;
      li.appendChild(strong);
      li.appendChild(span);
      list.appendChild(li);
    });
    return list;
  }

  async function openSkillZipFolder(statusLine) {
    try {
      const res = await requestJson("/api/assistant/connect/open-skill-zip", { method: "POST" });
      if (mountedHost && currentData) {
        currentData.skill_zip_ready = true;
        if (res.path) currentData.skill_zip_path = res.path;
      }
      if (statusLine) {
        statusLine.textContent = res.opened
          ? `已打开文件夹并高亮技能包：${res.path}`
          : `技能包已生成：${res.path}`;
      }
    } catch (error) {
      if (statusLine) statusLine.textContent = error.message || "打开文件夹失败，请点右侧下载技能包。";
    }
  }

  function renderTabPanel(panel, data, statusLine) {
    panel.textContent = "";
    if (activeTab === "doubao") {
      panel.appendChild(
        buildStepsList([
          {
            title: "第一步：获取技能包 tiyouju-skill.zip",
            desc: "点下方「打开技能包所在目录」直接定位现成压缩包（或直接点击下载）。",
          },
          {
            title: "第二步：拖进豆包电脑版",
            desc: "打开豆包「技能·连接器·伙伴 → 我的技能 → 新建 → 上传技能」，把压缩包拖进去即可。",
          },
          {
            title: "第三步：发一句话开始用",
            desc: "点「复制开场指令」发给豆包，它会自动读图查库、核对题目并整理 OneNote 笔记。",
          },
        ])
      );
      const actions = document.createElement("div");
      actions.className = "ai-connect-actions";
      actions.appendChild(
        makeButton("打开技能包所在目录", "button primary", () => {
          void openSkillZipFolder(statusLine);
        })
      );
      const dl = document.createElement("a");
      dl.className = "button";
      dl.href = "/api/assistant/connect/skill-zip";
      dl.textContent = "下载 tiyouju-skill.zip";
      actions.appendChild(dl);
      actions.appendChild(
        makeButton("复制 AI 开场指令", "button", (e) => {
          void copyText(data.prompt_intro, e.currentTarget, "复制 AI 开场指令");
        })
      );
      panel.appendChild(actions);
      return;
    }

    if (activeTab === "workbuddy_minimax") {
      panel.appendChild(
        buildStepsList([
          {
            title: "腾讯 WorkBuddy（自动写入，需点一次启用）",
            desc: "已优先为你写入 ~/.workbuddy/mcp.json 与技能目录；打开 WorkBuddy「连接器管理」对 tiyouju 点一下「信任并启用」即可。",
          },
          {
            title: "MiniMax Agent 桌面版（支持拖入技能包或 MCP）",
            desc: "在 Expert Agents / 导入 Skill 直接拖入 tiyouju-skill.zip，或在 MCP 设置面板粘贴下方配置 JSON。",
          },
        ])
      );
      const actions = document.createElement("div");
      actions.className = "ai-connect-actions";
      actions.appendChild(
        makeButton("一键复制 MCP 配置 (JSON)", "button primary", (e) => {
          void copyText(data.mcp_json, e.currentTarget, "一键复制 MCP 配置 (JSON)");
        })
      );
      actions.appendChild(
        makeButton("打开技能包所在目录", "button", () => {
          void openSkillZipFolder(statusLine);
        })
      );
      actions.appendChild(
        makeButton("复制 AI 开场指令", "button", (e) => {
          void copyText(data.prompt_intro, e.currentTarget, "复制 AI 开场指令");
        })
      );
      panel.appendChild(actions);
      return;
    }

    panel.appendChild(
      buildStepsList([
        {
          title: "标准 MCP 客户端通用配置（已带当前电脑真实路径）",
          desc: "Cursor、Trae 国际/国内版、Claude、Qwen、Cherry Studio、Chatbox 等，点一次复制粘贴到 MCP 配置即接入 30+ 题库工具。",
        },
      ])
    );
    const pre = document.createElement("pre");
    pre.className = "ai-connect-code";
    const code = document.createElement("code");
    code.textContent = data.mcp_json || "{}";
    pre.appendChild(code);
    panel.appendChild(pre);

    const actions = document.createElement("div");
    actions.className = "ai-connect-actions";
    actions.appendChild(
      makeButton("一键复制 MCP 配置 (JSON)", "button primary", (e) => {
        void copyText(data.mcp_json, e.currentTarget, "一键复制 MCP 配置 (JSON)");
      })
    );
    actions.appendChild(
      makeButton("复制 Codex 一行命令", "button", (e) => {
        void copyText(data.codex_command, e.currentTarget, "复制 Codex 一行命令");
      })
    );
    actions.appendChild(
      makeButton("复制 AI 开场指令", "button", (e) => {
        void copyText(data.prompt_intro, e.currentTarget, "复制 AI 开场指令");
      })
    );
    panel.appendChild(actions);
  }

  function renderCard(host, data) {
    currentData = data;
    host.textContent = "";

    const section = document.createElement("section");
    section.className = "settings-section ai-connect-card";

    const head = document.createElement("div");
    head.className = "ai-connect-head";
    const titleWrap = document.createElement("div");
    const h3 = document.createElement("h3");
    h3.textContent = "连接我的 AI 助手";
    const sub = document.createElement("p");
    sub.className = "ai-connect-lead";
    sub.textContent = "零代码一键接管题库与 OneNote：支持豆包电脑版、腾讯 WorkBuddy、MiniMax、Trae、Cursor 与 MCP 客户端。";
    titleWrap.appendChild(h3);
    titleWrap.appendChild(sub);

    const rescanBtn = makeButton("重新扫描本机助手", "button", async () => {
      rescanBtn.disabled = true;
      statusLine.textContent = "正在扫描本机已安装的 AI 客户端并补齐技能/MCP…";
      try {
        const res = await requestJson("/api/assistant/connect/setup", { method: "POST" });
        if (res && res.connect) {
          renderCard(host, res.connect);
          return;
        }
        statusLine.textContent = "扫描完成。";
      } catch (error) {
        statusLine.textContent = error.message || "扫描接入失败。";
      } finally {
        rescanBtn.disabled = false;
      }
    });
    head.appendChild(titleWrap);
    head.appendChild(rescanBtn);
    section.appendChild(head);

    const statusRow = document.createElement("div");
    statusRow.className = "ai-connect-badges";
    renderBadges(data, statusRow);
    section.appendChild(statusRow);

    const tabBar = document.createElement("div");
    tabBar.className = "ai-connect-tabs";
    tabBar.setAttribute("role", "tablist");

    const panel = document.createElement("div");
    panel.className = "ai-connect-panel";

    const statusLine = document.createElement("p");
    statusLine.className = "ai-connect-status";

    TABS.forEach((tab) => {
      const tabBtn = makeButton(tab.label, "ai-connect-tab", () => {
        activeTab = tab.id;
        tabBar.querySelectorAll(".ai-connect-tab").forEach((el) => {
          const selected = el === tabBtn;
          el.setAttribute("aria-selected", selected ? "true" : "false");
        });
        renderTabPanel(panel, data, statusLine);
      });
      tabBtn.setAttribute("role", "tab");
      tabBtn.setAttribute("aria-selected", tab.id === activeTab ? "true" : "false");
      tabBar.appendChild(tabBtn);
    });
    section.appendChild(tabBar);
    section.appendChild(panel);
    renderTabPanel(panel, data, statusLine);
    section.appendChild(statusLine);

    const promptWrap = document.createElement("div");
    promptWrap.className = "ai-connect-examples";
    const promptTitle = document.createElement("span");
    promptTitle.className = "ai-connect-examples-title";
    promptTitle.textContent = "接好后试试这样问 AI（点击即复制）：";
    promptWrap.appendChild(promptTitle);

    const pills = document.createElement("div");
    pills.className = "ai-connect-example-pills";
    (data.example_prompts || []).forEach((item) => {
      const chip = makeButton(item.label, "ai-connect-chip", (e) => {
        void copyText(item.prompt, e.currentTarget, item.label);
      });
      chip.title = item.prompt;
      pills.appendChild(chip);
    });
    promptWrap.appendChild(pills);
    section.appendChild(promptWrap);

    const modeTip = document.createElement("p");
    modeTip.className = "settings-footnote";
    modeTip.textContent = "两种配合方式：0 密钥即可直接整理题库、补解析和处理文字版 PDF/Word；如果常录扫描卷、拍照卷或带几何图的试卷，建议在「API 配置」补填一个 MinerU，由 MinerU 先切题抠配图，再交由 AI 助手校对和写解析，效果更好。";
    section.appendChild(modeTip);

    const safety = document.createElement("p");
    safety.className = "settings-footnote";
    safety.textContent = "安全承诺：AI 助手仅在本机运行，绝不读取 API 密钥，也不会擅自删题或篡改已入库题面（修改均需你点头采纳）。";
    section.appendChild(safety);

    host.appendChild(section);
  }

  async function mount(container) {
    if (!container) return;
    mountedHost = container;
    if (!currentData) {
      container.textContent = "";
      const loading = document.createElement("p");
      loading.className = "settings-footnote";
      loading.textContent = "正在检查本机 AI 助手接入状态…";
      container.appendChild(loading);
    }
    try {
      const data = await requestJson("/api/assistant/connect");
      if (mountedHost === container) renderCard(container, data);
    } catch (error) {
      container.textContent = "";
      const err = document.createElement("p");
      err.className = "settings-footnote";
      err.textContent = error.message || "暂未连上题有据本地服务。";
      container.appendChild(err);
    }
  }

  async function refresh() {
    if (mountedHost) await mount(mountedHost);
  }

  global.AssistantConnect = { mount, refresh };
})(window);
