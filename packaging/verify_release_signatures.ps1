param(
    [Parameter(Mandatory)] [string]$BundleRoot,
    [Parameter(Mandatory)] [string]$InstallerPath
)

$ErrorActionPreference = 'Stop'
$paths = @(
    (Join-Path $BundleRoot 'QuestionBankCard.exe'),
    (Join-Path $BundleRoot 'tiyouju.exe'),
    $InstallerPath
)
$publisher = $null
foreach ($path in $paths) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "缺少待发布文件: $path"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $path
    if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate) {
        throw "签名无效，拒绝发布: $path ($($signature.Status))"
    }
    if (-not $signature.TimeStamperCertificate) {
        throw "缺少签名时间戳，拒绝发布: $path"
    }
    $thumbprint = $signature.SignerCertificate.Thumbprint
    if ([string]::IsNullOrWhiteSpace($thumbprint)) {
        throw "缺少发布者证书标识，拒绝发布: $path"
    }
    if ($publisher -and $publisher -ne $thumbprint) {
        throw "安装包与主程序的发布者证书不一致，拒绝发布: $path"
    }
    $publisher = $thumbprint
    Write-Host "签名和时间戳验证通过: $(Split-Path -Leaf $path)"
}
