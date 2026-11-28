/* 两个工作台共用更新状态，下载不自动安装。 */
(function (root) {
  "use strict";
  function create({ request, onChange, confirmInstall }) {
    let latest = null;
    let progress = { status: "idle", active: false };
    let checking = false;
    let pending = false;
    let confirming = false;
    let message = "检查新版本后可下载更新。";
    let error = false;
    let pollTask = null;
    const emit = () => {
      const status = progress.status;
      const busy = pending || progress.active || status === "succeeded";
      const ready = Boolean(progress.can_install);
      const available = Boolean(latest && latest.update_available);
      const labels = { preparing: "正在准备…", downloading: "正在下载…", verifying: "正在校验…",
        cancelling: "正在取消…", backing_up: "正在备份…", launching: "正在启动安装…", succeeded: "安装已启动" };
      onChange({ latest, progress, checking, message, error,
        visible: available || busy || ready || status === "cancelled" || status === "failed",
        disabled: busy || checking || confirming || (!ready && !available),
        label: confirming ? "等待确认…" : (pending ? "正在提交…" : (labels[status] || (ready ? "安装更新" : "下载更新"))),
        cancelVisible: Boolean(progress.active && !progress.installing) || (pending && !ready),
        cancelDisabled: pending || status === "cancelling",
        checkDisabled: busy || checking || confirming,
      });
    };
    const sync = (p) => {
      progress = p || { status: "idle", active: false };
      if (progress.status !== "idle") {
        message = progress.error || progress.stage_label || "正在更新…";
        error = progress.status === "failed";
      }
      emit();
    };
    const report = (err) => {
      message = err.message || "暂时无法连接更新服务，请稍后重试。";
      error = true;
      emit();
    };
    const refresh = async () => {
      const p = await request("/api/update/progress");
      sync(p);
      if (p.active) void poll();
      return p;
    };
    const poll = () => {
      if (pollTask) return pollTask;
      pollTask = (async () => {
        try {
          do {
            await new Promise((resolve) => setTimeout(resolve, 500));
            sync(await request("/api/update/progress"));
          } while (progress.active);
        } catch (err) {
          // 读取失败不代表后台下载已停止。保留禁用状态和取消入口。
          report(new Error(`进度暂时无法读取，请重新打开此页重试。${err.message || ""}`));
        } finally { pollTask = null; }
        return progress;
      })();
      return pollTask;
    };
    const updateLatest = (data) => {
      if (data.error) { latest = null; report(new Error(data.error)); return; }
      latest = data;
      if (!progress.active && !progress.can_install && progress.status !== "succeeded") {
        message = data.skipped ? "已关闭自动检查，需要时可手动检查更新。"
          : data.update_available ? `发现新版本 ${data.latest}（当前 ${data.current}）。下载完成后由你确认安装。`
            : data.latest ? `当前版本 ${data.current} 已是最新。` : "检查新版本后可下载更新。";
        error = false;
      }
      emit();
    };
    const check = async (auto = false) => {
      if (checking || pending || progress.active || progress.status === "succeeded") return;
      checking = true;
      message = "正在检查新版本…";
      error = false;
      emit();
      try { updateLatest(await request(`/api/update/check${auto ? "?auto=1" : ""}`, { method: "POST" })); }
      catch (err) { latest = null; report(err); }
      finally { checking = false; emit(); }
    };
    const start = async () => {
      if (pending || confirming || checking || progress.active || progress.status === "succeeded") return;
      const installing = Boolean(progress.can_install);
      if (!installing && !latest?.update_available) return;
      if (installing) {
        confirming = true;
        emit();
        try {
          if (!await confirmInstall(progress.version)) return;
        } finally { confirming = false; emit(); }
      }
      pending = true;
      emit();
      try {
        const p = await request(installing ? "/api/update/install/confirm" : "/api/update/download/start", {
          method: "POST", ...(installing ? { body: { confirmed: true } } : {}) });
        sync(p);
        if (p.active) void poll();
      } catch (err) {
        // 请求可能已被服务器接收；先恢复真实状态，不能直接允许重复点击。
        try { await refresh(); } catch (_) { progress = { ...progress, status: "preparing", active: true, installing }; }
        report(err);
      } finally { pending = false; emit(); }
    };
    const cancel = async () => {
      if (pending || progress.installing || !progress.active || progress.status === "cancelling") return;
      pending = true;
      emit();
      try { sync(await request("/api/update/download/cancel", { method: "POST" })); void poll(); }
      catch (err) { report(err); }
      finally { pending = false; emit(); }
    };
    return { check, start, cancel, refresh, updateLatest, sync };
  }
  const api = { create };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.QBUpdate = api;
})(typeof window !== "undefined" ? window : globalThis);
