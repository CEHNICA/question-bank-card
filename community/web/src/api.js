// 所有接口同源；写操作带 X-TYJ 头，Worker 据此拒绝跨站请求。
export async function api(path, { method = "GET", body, form } = {}) {
  const headers = {};
  let payload;
  if (method !== "GET") headers["X-TYJ"] = "1";
  if (form) payload = form;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const response = await fetch(path, { method, headers, body: payload, credentials: "same-origin" });
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    const error = new Error(data?.error || "这次操作没完成，请稍后再试。");
    error.status = response.status;
    throw error;
  }
  return data || {};
}

export const state = {
  config: { site_name: "题有据社区", turnstile_site_key: "", max_pack_mb: 50 },
  user: null,
};

export async function loadSession() {
  const [config, me] = await Promise.all([api("/api/config").catch(() => null), api("/api/me").catch(() => null)]);
  if (config) state.config = config;
  state.user = me?.user || null;
}

let turnstileLoading = null;
function loadTurnstileScript() {
  if (window.turnstile) return Promise.resolve();
  if (!turnstileLoading) {
    turnstileLoading = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
      script.async = true;
      script.onload = () => resolve();
      script.onerror = () => {
        turnstileLoading = null;
        reject(new Error("人机验证加载失败，请检查网络后刷新。"));
      };
      document.head.append(script);
    });
  }
  return turnstileLoading;
}

/** 在容器里放一个 Turnstile 小组件；返回取当前令牌和重置的方法。 */
export async function mountTurnstile(container) {
  let token = "";
  let widgetId = null;
  if (!state.config.turnstile_site_key) {
    container.textContent = "网站还没配置人机验证。";
    return { token: () => "", reset() {} };
  }
  await loadTurnstileScript();
  widgetId = window.turnstile.render(container, {
    sitekey: state.config.turnstile_site_key,
    language: "zh-cn",
    callback: (value) => {
      token = value;
    },
    "expired-callback": () => {
      token = "";
    },
  });
  return {
    token: () => token,
    reset() {
      token = "";
      if (widgetId !== null) window.turnstile.reset(widgetId);
    },
  };
}
