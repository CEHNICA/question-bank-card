// Tailwind 预编译配置：只覆盖 Eagle 工作台及其依赖的前端源文件。
// 路径相对于本文件所在目录（frontend/）。
//
// 重建 frontend/eagle/tailwind.css：
//   cd frontend
//   npx tailwindcss@3.4.17 -c tailwind.config.js -i tailwind.input.css \
//       -o eagle/tailwind.css --minify
// 改完源码里的 class 后必须重跑一次，否则新样式不会进产物。
module.exports = {
  content: [
    "./eagle/index.html",
    "./qb-render.js",
    "./question-locator.js",
    "./exam-export.js",
    "./exam-layout.js",
    "./eagle-solution-editor.js",
    "./browser-interactions.js",
    "./eagle/eagle-print-preview.js",
  ],
  theme: {
    extend: {
      // HTML 里大量用了 backdrop-blur-xs；默认主题没有 xs，不补就会 silently 没模糊。
      backdropBlur: { xs: "2px" },
    },
  },
  plugins: [],
};
