/* 录题上传暂存与拍照页序预处理模块。
 *
 * 负责在上传进入后端流水线前完成：
 *   1. 文件自然序排列与数量/体积统计
 *   2. 拍照原图 90° 旋转后的像素重编码（避免横拍试卷入库后整页歪斜）
 *   3. 统一的导入前摘要与云端处理授权文案生成
 */
(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.QBIntakeStaging = value;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const PHOTO_EXTS = new Set(["jpg", "jpeg", "png", "webp"]);

  function fileExtension(name) {
    const text = String(name || "");
    const dot = text.lastIndexOf(".");
    return dot >= 0 ? text.slice(dot + 1).toLowerCase() : "";
  }

  function isPhotoFile(file) {
    return PHOTO_EXTS.has(fileExtension(file?.name));
  }

  function formatFileSize(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return "大小未知";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${Math.max(0.1, bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function naturalCompare(a, b) {
    return String(a?.name || "").localeCompare(String(b?.name || ""), "zh-Hans-CN", {
      numeric: true,
      sensitivity: "base",
    });
  }

  function sortStagingEntries(entries) {
    return [...(entries || [])].sort((left, right) => naturalCompare(left.file, right.file));
  }

  /**
   * 规范化 90° 顺时针旋转步数（0..3）
   * @param {number} turns 旋转次数
   * @returns {number} 0..3
   */
  function normalizeTurns(turns) {
    const intTurns = Number.isFinite(turns) ? Math.trunc(turns) : 0;
    return ((intTurns % 4) + 4) % 4;
  }

  /**
   * 将图片按顺时针 90° 整数倍重新编码为新 File；未旋转或环境不支持 Canvas 时返回原文件
   * @param {File} file 原始图片文件
   * @param {number} quarterTurns 顺时针旋转次数（1=90°, 2=180°, 3=270°）
   * @returns {Promise<File>} 处理后的 File
   */
  async function rotateImageFile(file, quarterTurns) {
    const turns = normalizeTurns(quarterTurns);
    if (!file || turns === 0 || !isPhotoFile(file)) return file;
    if (typeof document === "undefined" || typeof createImageBitmap !== "function") return file;

    let bitmap = null;
    try {
      bitmap = await createImageBitmap(file);
      const swap = turns % 2 === 1;
      const width = swap ? bitmap.height : bitmap.width;
      const height = swap ? bitmap.width : bitmap.height;
      const canvas = document.createElement("canvas");
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext("2d");
      if (!ctx) return file;

      ctx.translate(width / 2, height / 2);
      ctx.rotate((turns * Math.PI) / 2);
      ctx.drawImage(bitmap, -bitmap.width / 2, -bitmap.height / 2);

      const mime = file.type && /^image\/(png|webp|jpeg)$/.test(file.type) ? file.type : "image/jpeg";
      const blob = await new Promise((resolve) => canvas.toBlob(resolve, mime, 0.92));
      if (!blob) return file;
      return new File([blob], file.name || "photo.jpg", {
        type: blob.type || mime,
        lastModified: file.lastModified || Date.now(),
      });
    } catch {
      return file;
    } finally {
      if (bitmap && typeof bitmap.close === "function") bitmap.close();
    }
  }

  /**
   * 生成上传暂存栏的清晰中文状态摘要
   * @param {object} params 暂存状态参数
   * @returns {{ countText: string, detailText: string }}
   */
  function buildStagingSummary({ files = [], materialType = "exam", enhance = true, cloudReady = false, allowCloud = false } = {}) {
    const list = Array.isArray(files) ? files : [];
    const totalBytes = list.reduce((sum, item) => sum + (Number(item?.size) || 0), 0);
    const kindLabel = materialType === "book" ? "整本教材/讲义" : "单套试卷/讲义";
    const enhanceLabel = enhance ? "开启扫描增亮" : "保留原图色彩";
    const modeLabel = cloudReady
      ? allowCloud
        ? "本地优先，必要时允许云切题"
        : "仅保留在本机处理"
      : "本机处理模式";
    return {
      countText: `共 ${list.length} 页照片（${formatFileSize(totalBytes)}）`,
      detailText: `${kindLabel} · ${enhanceLabel} · ${modeLabel}`,
    };
  }

  return {
    isPhotoFile,
    formatFileSize,
    naturalCompare,
    sortStagingEntries,
    normalizeTurns,
    rotateImageFile,
    buildStagingSummary,
  };
});
