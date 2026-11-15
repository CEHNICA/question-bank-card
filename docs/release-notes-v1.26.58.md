# 1.26.58 —— 任务栏图标改回 TUG

## 这一版忙了什么

打开软件后，Windows 任务栏里不再是浏览器默认图标，而是题有据的 TUG 图标。

---

## 主要改动

### 1. Eagle 页补上 favicon
- 启动默认打开 `/eagle`；这一页原先没有 `<link rel="icon">`。
- Edge / Chrome 的 `--app` 窗口会按页面 favicon 画任务栏图标，缺了就掉成浏览器图标。
- 现已挂上 `/favicon.png` 与 `/app-icon-256.png`。

### 2. 宿主再强制设一次窗口图标
- 找到应用窗口句柄后，用打包进安装包的 `assets/app.ico` 发 `WM_SETICON`。
- 启动约两秒后再刷一次，防止页面加载过程中被浏览器改回默认图标。
