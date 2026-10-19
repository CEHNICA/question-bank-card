#Requires -Version 5.1
<#
.SYNOPSIS
    Package this machine's DeepSeek Harness (DSH) data into one migratable zip.

.DESCRIPTION
    Layout produced:
      package\dsh-home          <- %USERPROFILE%\.dsh  (sessions, profiles, plugins, attachments, model config, credentials)
      package\electron-userdata <- %APPDATA%\@deepseek-ai\dsh-desktop (desktop settings)
      package\app               <- optional program files (~1 GB, for offline moves)
      package\workspace         <- optional workspace sources
    Plus manifest.json, README-zh.txt and restore-dsh.ps1.

    NOTE: console text is intentionally ASCII so the script runs identically under
    Windows PowerShell 5.1 and PowerShell 7 regardless of code page.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\package-dsh.ps1
.EXAMPLE
    .\package-dsh.ps1 -IncludeApp -IncludeWorkspace -OutRoot D:\dsh-migration
#>
[CmdletBinding()]
param(
    [string] $OutRoot = (Join-Path $PSScriptRoot 'out'),   # output directory
    [switch] $IncludeApp,                                   # also copy ~1 GB program files
    [switch] $IncludeWebState,                              # include Electron login/web state
    [switch] $IncludeWorkspace,                             # also copy the workspace sources
    [string] $WorkspacePath = 'D:\question_card_bank',
    [switch] $ExcludeCredentials,                           # skip .credentials.yaml
    [switch] $KeepStage                                     # keep staging dir after zipping
)

$ErrorActionPreference = 'Stop'

function Section($t) { Write-Host ''; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Ok($t)     { Write-Host "  [OK]  $t" -ForegroundColor Green }
function Warn2($t)   { Write-Host "  [!]   $t" -ForegroundColor Yellow }
function Info($t)    { Write-Host "        $t" -ForegroundColor DarkGray }

# ---------- 1. locate sources ----------
$appDir       = Join-Path $env:LOCALAPPDATA 'Programs\DeepSeek Harness'
$dshHome      = Join-Path $env:USERPROFILE '.dsh'
$electronRoot = Join-Path $env:APPDATA '@deepseek-ai\dsh-desktop'

$appVersion = 'unknown'
$verFile = Join-Path $appDir 'version'
if (Test-Path -LiteralPath $verFile) { $appVersion = (Get-Content -LiteralPath $verFile -Raw).Trim() }
if ($appVersion -eq 'unknown' -and (Test-Path -LiteralPath $appDir)) {
    $vi = (Get-Item -LiteralPath (Join-Path $appDir 'DeepSeek Harness.exe')).VersionInfo
    if ($vi.ProductVersion) { $appVersion = $vi.ProductVersion.Trim() }
}
if (-not (Test-Path -LiteralPath $dshHome)) { throw "DSH home not found: $dshHome" }

# ---------- 2. staging ----------
$stamp     = (Get-Date).ToString('yyyyMMdd-HHmmss')
$stageName = "dsh-migration-$appVersion-$stamp"
$stage     = Join-Path $OutRoot $stageName
$pkg       = Join-Path $stage 'package'
$zipPath   = Join-Path $OutRoot "$stageName.zip"

if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory -Path $pkg -Force | Out-Null

Write-Host ''
Write-Host '==============================================' -ForegroundColor DarkCyan
Write-Host '  DeepSeek Harness migration packager' -ForegroundColor Cyan
Write-Host '==============================================' -ForegroundColor DarkCyan
Info "version   : $appVersion"
Info "source    : $env:COMPUTERNAME \ $env:USERNAME"
Info "output    : $zipPath"

if (Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match 'DeepSeek' }) {
    Warn2 'DeepSeek Harness is still running; locked files will be skipped.'
    Info  'Close it (including the tray icon) and run again for a clean snapshot.'
}

# ---------- 3. copy ----------
function Copy-Tree {
    param(
        [Parameter(Mandatory)][string] $Source,
        [Parameter(Mandatory)][string] $Dest,
        [string[]] $ExcludeDirs  = @(),
        [string[]] $ExcludeFiles = @()
    )
    if (-not (Test-Path -LiteralPath $Source)) { Warn2 "skip (missing): $Source"; return }
    New-Item -ItemType Directory -Path $Dest -Force | Out-Null
    $a = @($Source, $Dest, '/E', '/R:1', '/W:1', '/XJ', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/MT:8')
    if ($ExcludeDirs.Count)  { $a += '/XD'; $a += $ExcludeDirs }
    if ($ExcludeFiles.Count) { $a += '/XF'; $a += $ExcludeFiles }
    $null = & robocopy @a
    if ($LASTEXITCODE -ge 8) { Warn2 "some files were in use (robocopy=$LASTEXITCODE): $Source" }
}

Section 'Copy DSH home (~/.dsh)'
$xFiles = @()
if ($ExcludeCredentials) { $xFiles += '.credentials.yaml' }
Copy-Tree -Source $dshHome -Dest (Join-Path $pkg 'dsh-home') -ExcludeFiles $xFiles
if ($ExcludeCredentials) { Ok '.credentials.yaml excluded as requested' }

Section 'Copy desktop settings'
$euExclude = @('Cache','Code Cache','GPUCache','GPUPersistentCache','GrShaderCache','ShaderCache',
               'DawnGraphiteCache','DawnWebGPUCache','blob_storage','Session Storage','Dictionaries',
               'Shared Dictionary','logs')
if ($IncludeWebState) {
    Warn2 'web state included; it is machine-bound and usually needs a fresh login'
} else {
    $euExclude += @('Partitions','Local Storage','WebStorage','Network')
    Info 'use -IncludeWebState to add Partitions / Local Storage / WebStorage'
}
Copy-Tree -Source $electronRoot -Dest (Join-Path $pkg 'electron-userdata') `
          -ExcludeDirs $euExclude -ExcludeFiles @('lockfile')

$appCopied = $false
if ($IncludeApp) {
    Section 'Copy program files (~1 GB, this takes a while)'
    Copy-Tree -Source $appDir -Dest (Join-Path $pkg 'app')
    $appCopied = $true
} else {
    Info 'use -IncludeApp to carry the program itself (offline move)'
}

$wsCopied = $false
if ($IncludeWorkspace) {
    Section "Copy workspace: $WorkspacePath"
    Copy-Tree -Source $WorkspacePath -Dest (Join-Path $pkg 'workspace') `
              -ExcludeDirs @('node_modules','.git','out','dist') -ExcludeFiles @('*.zip')
    $wsCopied = $true
}

# ---------- 4. manifest ----------
Section 'Write manifest.json'
function Measure-Tree {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $m = Get-ChildItem -LiteralPath $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
         Measure-Object -Property Length -Sum
    return [ordered]@{ files = $m.Count; bytes = [int64]$m.Sum }
}
$components = [ordered]@{}
foreach ($c in @('dsh-home','electron-userdata','app','workspace')) {
    $p = Join-Path $pkg $c
    if (Test-Path -LiteralPath $p) { $components[$c] = Measure-Tree -Path $p }
}
$manifest = [ordered]@{
    schema     = 1
    tool       = 'package-dsh.ps1'
    createdAt  = (Get-Date).ToString('o')
    appVersion = $appVersion
    sourceHost = [ordered]@{ computer = $env:COMPUTERNAME; user = $env:USERNAME
                             os = [System.Environment]::OSVersion.VersionString }
    sourcePaths = [ordered]@{ appDirectory = $appDir; dshHome = $dshHome
                              electronUserData = $electronRoot; workspace = $WorkspacePath }
    options    = [ordered]@{ includeApp = [bool]$IncludeApp
                             includeWebState = [bool]$IncludeWebState
                             includeWorkspace = [bool]$IncludeWorkspace
                             excludeCredentials = [bool]$ExcludeCredentials }
    components = $components
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $stage 'manifest.json') -Encoding UTF8
Ok 'manifest.json written'

# ---------- 5. ship restore script + Chinese readme ----------
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'restore-dsh.ps1') `
          -Destination (Join-Path $stage 'restore-dsh.ps1') -Force

$tpl = Join-Path $PSScriptRoot 'readme.template.zh.txt'
if (Test-Path -LiteralPath $tpl) {
    $text = Get-Content -LiteralPath $tpl -Raw -Encoding UTF8
    $map = [ordered]@{
        '{{VERSION}}'   = $appVersion
        '{{STAMP}}'     = $stamp
        '{{COMPUTER}}'  = $env:COMPUTERNAME
        '{{USER}}'      = $env:USERNAME
        '{{CRED_NOTE}}' = $(if ($ExcludeCredentials) { 'NOT included - log in again and re-enter keys on the new PC' }
                            else { 'INCLUDED - the zip holds login credentials and API keys; move it on trusted media, do not upload it' })
        '{{APP_STATE}}' = $(if ($appCopied) { 'included' } else { 'not included - install DSH on the new PC' })
        '{{WS_STATE}}'  = $(if ($wsCopied)  { 'included' } else { 'not included' })
        '{{WS_PATH}}'   = $WorkspacePath
    }
    foreach ($k in $map.Keys) { $text = $text.Replace($k, [string]$map[$k]) }
    Set-Content -LiteralPath (Join-Path $stage 'README-zh.txt') -Value $text -Encoding UTF8
    Ok 'README-zh.txt written'
} else {
    Warn2 "readme template missing: $tpl"
}

# ---------- 6. zip ----------
Section 'Compress'
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
$zipped = $false
try {
    Add-Type -AssemblyName System.IO.Compression -ErrorAction Stop
    Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $stage, $zipPath, [System.IO.Compression.CompressionLevel]::Optimal, $false)
    $zipped = $true
    Ok 'zipped with .NET'
} catch {
    Warn2 ".NET zip unavailable ($($_.Exception.Message)); falling back to tar"
    & tar.exe -a -c -f $zipPath -C (Split-Path -Parent $stage) (Split-Path -Leaf $stage)
    if ($LASTEXITCODE -eq 0) { $zipped = $true; Ok 'zipped with tar' }
}
if (-not $zipped) { throw "zip failed; staging kept at $stage" }

$zipMB = [math]::Round((Get-Item -LiteralPath $zipPath).Length / 1MB, 2)
Ok ("zip: $zipPath ($zipMB MB)")

if (-not $KeepStage) {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    Ok 'staging dir removed (use -KeepStage to keep it)'
}

Write-Host ''
Write-Host '==============================================' -ForegroundColor Green
Write-Host '  DONE' -ForegroundColor Green
Write-Host '==============================================' -ForegroundColor Green
Write-Host "  zip : $zipPath"
Write-Host "  size: $zipMB MB"
Write-Host ''
Write-Host '  Next: copy the zip to the other PC, extract it, run restore-dsh.ps1.'
Write-Host ''
