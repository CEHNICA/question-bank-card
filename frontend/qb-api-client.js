/* 题有据前端统一 API 客户端与按资源串行修改队列。
 *
 * 统一处理：
 *   1. X-QB-Request: 1 安全请求头（所有非 GET/HEAD 写请求自动携带）
 *   2. Content-Type: application/json（普通对象载荷自动序列化）
 *   3. 结构化错误信息与 409 版本冲突标记
 *   4. 按资源键（如 paper:<id>）串行排队执行写请求，避免 layout_revision / content_revision 竞态冲突
 */
(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.QBApi = value;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

  /**
   * 从异常对象提取可展示给老师的中文错误说明
   * @param {unknown} error 捕获到的异常
   * @param {string} fallback 兜底提示
   * @returns {string} 错误提示文本
   */
  function errorMessage(error, fallback = "请求失败，请稍后重试") {
    if (!error) return fallback;
    if (typeof error === "string") return error || fallback;
    if (typeof error === "object") {
      if (typeof error.message === "string" && error.message.trim()) return error.message.trim();
      if (error.data && typeof error.data.error === "string" && error.data.error.trim()) {
        return error.data.error.trim();
      }
    }
    return fallback;
  }

  /**
   * 判断错误是否为版本/状态并发冲突（HTTP 409）
   * @param {unknown} error 异常对象
   * @returns {boolean} 是否为冲突错误
   */
  function isConflictError(error) {
    return Boolean(error && typeof error === "object" && (error.status === 409 || error.code === "CONFLICT"));
  }

  /**
   * 发起题有据后端 API 请求并解析 JSON 响应
   * @param {string} path 请求路径
   * @param {object} [options] 请求选项
   * @returns {Promise<any>} 解析后的响应数据
   */
  async function request(path, { method = "GET", body, form, headers, signal, cache = "no-store", fetchImpl } = {}) {
    const normalizedMethod = String(method || "GET").toUpperCase();
    const requestHeaders = Object.assign({}, headers || {});
    if (!SAFE_METHODS.has(normalizedMethod)) {
      requestHeaders["X-QB-Request"] = "1";
    }

    const options = {
      method: normalizedMethod,
      headers: requestHeaders,
      cache,
    };
    if (signal) options.signal = signal;

    if (form !== undefined) {
      options.body = form;
    } else if (body !== undefined) {
      if (typeof FormData !== "undefined" && body instanceof FormData) {
        options.body = body;
      } else {
        if (!requestHeaders["Content-Type"]) {
          requestHeaders["Content-Type"] = "application/json";
        }
        options.body = typeof body === "string" ? body : JSON.stringify(body);
      }
    }

    const doFetch = fetchImpl || fetch;
    const response = await doFetch(path, options);
    let data = {};
    try {
      data = await response.json();
    } catch {
      /* 允许 204 或空响应体 */
    }
    if (!response.ok) {
      const error = new Error((data && data.error) || `请求失败（${response.status}）`);
      error.status = response.status;
      error.data = data;
      if (response.status === 409) error.code = "CONFLICT";
      throw error;
    }
    return data;
  }

  /**
   * 创建按资源键串行化的写操作队列，防止并发保存导致版本号冲突
   * @returns {{ enqueue: Function, pendingCount: Function }}
   */
  function createMutationQueue() {
    const tails = new Map();

    function enqueue(resourceKey, task) {
      const key = String(resourceKey || "default");
      const previous = tails.get(key) || Promise.resolve();
      const run = previous.catch(() => undefined).then(() => task());
      const settled = run.catch(() => undefined);
      tails.set(key, settled);
      settled.finally(() => {
        if (tails.get(key) === settled) tails.delete(key);
      });
      return run;
    }

    function pendingCount(resourceKey) {
      if (resourceKey === undefined) return tails.size;
      return tails.has(String(resourceKey)) ? 1 : 0;
    }

    return { enqueue, pendingCount };
  }

  return {
    request,
    errorMessage,
    isConflictError,
    createMutationQueue,
  };
});
