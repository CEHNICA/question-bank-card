DeepSeek Harness 迁移包
================================================================
版本      : {{VERSION}}
打包时间  : {{STAMP}}
源机器    : {{COMPUTER}} \ {{USER}}

包内结构
----------------------------------------------------------------
package\dsh-home           你的 DSH 数据（会话 / 插件 / 附件 / 模型配置 / 凭据）
package\electron-userdata  桌面端设置
package\app                程序本体 —— 本次：{{APP_STATE}}
package\workspace          工作区源码 —— 本次：{{WS_STATE}}
manifest.json              清单：版本、来源路径、各部分体积
restore-dsh.ps1            一键还原脚本
README-zh.txt              本文件

新电脑上怎么用
----------------------------------------------------------------
1) 先安装 DeepSeek Harness（或用还原脚本的 -InstallApp 连程序一起装）。
2) 把压缩包复制过去，解压到任意目录。
3) 在解压目录执行：
       powershell -NoProfile -ExecutionPolicy Bypass -File .\restore-dsh.ps1
4) 打开 DeepSeek Harness。

注意事项
----------------------------------------------------------------
[凭据] {{CRED_NOTE}}
[登录] 网页登录 Cookie 由 Windows DPAPI 加密，绑定本机账号，换电脑通常要重新登录，属正常现象。
[插件] 插件随 profile 一起迁移（dsh-home\profiles\desktop），新电脑上直接可用。
[会话] 会话按工作区绝对路径归档，例如 D:\question_card_bank 对应目录名 --D-question_card_bank--。
       想要老会话原样出现，请把工作区放回同一路径；换了路径就在还原时加
       -Workspace '<新路径>'，脚本会自动重命名会话归档。
[覆盖] 还原默认合并覆盖；要整体替换并自动备份旧数据，加 -Mode Replace。
================================================================
