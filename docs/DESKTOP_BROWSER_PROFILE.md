# 桌面窗口的密码保存边界

Windows 安装版使用 Edge 或 Chrome 的应用窗口，并把浏览器配置放进题有据自己的 `browser-profile-…` 目录。启动固定使用该目录的 `Default` 配置，只把 `credentials_enable_service` 与 `credentials_enable_autosignin` 两项设为 `false`，关闭这个窗口的浏览器密码保存及自动登录；不修改用户普通 Edge/Chrome 的偏好、策略或注册表。

升级已有专用配置时，保留其余偏好，原子替换 `Default/Preferences`。损坏或无法写入的文件不会被重置，会记录不含文件内容的失败提示。不会读取浏览器 `Login Data`、Cookies 或应用密钥文件，也不会删除过去已经存在的浏览器密码。密钥继续由应用按 Windows 当前用户加密保存，输入仍遮罩、已保存的值不会回显。

读题服务与标签答案的 API Key 输入都声明 `autocomplete="off"`，关闭自动大小写与自动纠正，并给部分第三方密码扩展提供忽略提示。这些 HTML 提示不能强制控制用户的普通浏览器或 Codex 内置浏览器；网站无权关闭客户端的密码管理器。对独立安装版的控制来自上述专用浏览器偏好，更新后需关闭并重开应用窗口才能生效。

实现参考：[Chromium 密码管理偏好定义](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/components/password_manager/core/common/password_manager_pref_names.h)。[Microsoft Edge 的密码保存说明](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-policies/passwordmanagerenabled)说明关闭密码管理器会停止保存新密码；本项目不写入该全局策略。

离线验证覆盖新专用配置、已有偏好保留、普通浏览器与其他文件不变、不读取登录数据库或 Cookies、重复启动不反复写入、损坏文件及原子替换失败的保留与临时文件清理，以及密钥输入遮罩和自动填写属性。
