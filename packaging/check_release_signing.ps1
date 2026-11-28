# 发布前只检查配置是否齐备，不请求签名服务、不输出凭据。
$ErrorActionPreference = 'Stop'

$missing = @('SIGNPATH_API_TOKEN', 'SIGNPATH_ORG_ID') | Where-Object {
    [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_))
}
if ($missing) {
    throw "正式发布需要受信任的代码签名。缺少 GitHub Actions Secrets: $($missing -join ', ')。请先按 CODE_SIGNING_POLICY.md 开通并配置 SignPath；不会跳过签名发布安装包。"
}
if ([string]::IsNullOrWhiteSpace($env:GITHUB_OUTPUT)) {
    throw '缺少 GITHUB_OUTPUT，无法向后续签名步骤传递配置。'
}

$projectSlug = @($env:SIGNPATH_PROJECT_SEC, $env:SIGNPATH_PROJECT_VAR, 'question-bank-card') |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1
$policySlug = @($env:SIGNPATH_POLICY_SEC, $env:SIGNPATH_POLICY_VAR, 'release-signing') |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1
foreach ($slug in @($projectSlug, $policySlug)) {
    if ($slug -notmatch '^[A-Za-z0-9_-]+$') {
        throw 'SignPath 项目或策略标识格式不正确。'
    }
}
"project_slug=$projectSlug" >> $env:GITHUB_OUTPUT
"policy_slug=$policySlug" >> $env:GITHUB_OUTPUT
Write-Host '签名配置已齐备，后续签名与证书验证必须全部通过才可发布。'
