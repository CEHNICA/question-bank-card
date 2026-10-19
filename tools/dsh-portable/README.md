# DeepSeek Harness 迁移打包工具

把本机的 DeepSeek Harness（DSH）连同你的会话、插件、设置和密钥打成一个压缩包，拷到另一台电脑一键还原。

## 一、打包（在本机）

双击 `package-dsh.cmd`，或在 PowerShell 里执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\package-dsh.ps1
```

常用参数：

| 参数 | 作用 |
| --- | --- |
| `-OutRoot D:\dsh-migration` | 指定输出目录，默认是本目录下的 `out` |
| `-IncludeApp` | 连约 1GB 的程序本体一起打包，新电脑无需联网安装 |
| `-IncludeWorkspace` | 额外打包工作区源码（默认 `D:\question_card_bank`） |
| `-IncludeWebState` | 带上 Electron 网页登录态（多与本机绑定，换电脑一般要重登） |
| `-ExcludeCredentials` | **不**打包 `.credentials.yaml`，新电脑要重新登录并重填密钥 |
| `-KeepStage` | 压缩后保留中间目录 |

打包时建议先退出 DSH；不退出也能跑，被占用的文件会被跳过并在终端提示。

产物在 `out\` 下：`dsh-migration-<版本>-<时间>.zip`，解开后包含

```
manifest.json               清单（版本、来源路径、各部分体积）
README-迁移说明.txt           给新电脑看的说明
restore-dsh.ps1              一键还原脚本
package\dsh-home            会话 / profile / 插件 / 附件 / 模型配置 / 凭据
package\electron-userdata    桌面端设置
package\app                 程序本体（用了 -IncludeApp 才有）
package\workspace           工作区源码（用了 -IncludeWorkspace 才有）
```

## 二、还原（在新电脑）

1. 先安装 DeepSeek Harness（或用 `-InstallApp` 连程序一起还原）。
2. 把 zip 拷过去并解压。
3. 在解压目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\restore-dsh.ps1
```

常用参数：

| 参数 | 作用 |
| --- | --- |
| `-Mode Replace` | 先把新电脑上已有的 `.dsh` 改名备份，再整体替换 |
| `-InstallApp` | 把包里的程序本体装到 `%LOCALAPPDATA%\Programs\DeepSeek Harness` |
| `-Workspace 'D:\question_card_bank'` | 工作区换了路径时，自动重命名会话归档目录 |
| `-SkipCredentials` | 不还原登录凭据 |
| `-WhatIfOnly` | 只演练不写入 |

## 三、必须知道的几件事

- **凭据**：`~\.dsh\.credentials.yaml` 里是登录凭据，默认打包。请用 U 盘等可信介质传输；不想带就加 `-ExcludeCredentials`。按项目规则，我不会读取或输出里面的密钥内容。
- **网页登录 Cookie 绑定本机**：Electron 用 Windows DPAPI 加密，换电脑大概率要重新登录一次，属正常现象。
- **会话按工作区绝对路径归档**：目录名如 `--D-question_card_bank--` 对应 `D:\question_card_bank`。想让老会话原样出现，就把工作区放回同一路径；换了路径就在还原时用 `-Workspace` 指定。
- **数据原位**：个人数据始终在 `C:\Users\<你>\.dsh`，Electron 设置在 `%APPDATA%\@deepseek-ai\dsh-desktop`，两者都不是安装在程序目录里，所以重装/升级软件不会丢数据。
