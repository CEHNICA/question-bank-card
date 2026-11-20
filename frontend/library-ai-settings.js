// 兼容层：保留 window.LibraryAISettings 接口，内部统一委托给 window.APISettings。
// 历史版本中「题库解题 API 设置」独立管理一套表单与密钥文件；架构统一后由
// frontend/api-settings.js 统一接管，此处仅保留兼容调用入口以免旧页面或脚本报错。
(function () {
  "use strict";

  const api = () => window.APISettings || null;

  async function mount(hostElement, options = {}) {
    if (!api()) return null;
    return api().mount(hostElement, options);
  }

  async function open(options = {}) {
    const dialog = document.getElementById("credentialDialog");
    if (api() && dialog) {
      return api().open({ ...options, section: "answering" });
    }
    if (typeof window.location?.assign === "function" && window.location.pathname !== "/settings") {
      window.location.assign("/settings#ai");
    }
    return null;
  }

  function hasUnsavedChanges() {
    return Boolean(api()?.hasUnsavedChanges?.());
  }

  function isMutating() {
    return Boolean(api()?.isMutating?.());
  }

  function hideSecrets() {
    api()?.hideSecrets?.();
  }

  async function discard() {
    if (!api()?.discard) return true;
    return api().discard();
  }

  function activate() {
    return true;
  }

  function deactivate() {
    hideSecrets();
    return true;
  }

  window.LibraryAISettings = Object.freeze({
    mount,
    open,
    activate,
    deactivate,
    hideSecrets,
    hasUnsavedChanges,
    isMutating,
    discard,
    // 统一密钥库模式下不再需要手动将读题密钥拷贝到解题端，保留空实现以兼容旧调用。
    setReadingKeys: () => {},
    setReadingService: () => {},
  });
})();
