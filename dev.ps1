<#
.SYNOPSIS
    题有据的开发环境入口。一条命令装好、跑检查、跑测试、打包、发布。

.DESCRIPTION
    这个项目只有一个人长期维护，工具链必须「一条命令就能走完」，
    免得每次开发都靠记忆拼命令、拼错一步就出事。

        .\dev.ps1 setup      装好开发环境（Python 虚拟环境 + 前端工具）
        .\dev.ps1 check      跑全部检查（规范 + 类型 + 三组测试）
        .\dev.ps1 test       只跑测试
        .\dev.ps1 lint       只跑代码规范检查
        .\dev.ps1 format     自动修能自动修的（规范 + 格式）
        .\dev.ps1 type       只跑类型检查
        .\dev.ps1 run        起一个本地开发实例（用临时数据，不碰真数据）
        .\dev.ps1 build      打出安装包和免安装包
        .\dev.ps1 release    发一个新版本（改版本号、打包、提交、打标签、发 Release）

.PARAMETER Task
    要做的事：setup / check / test / lint / format / type / run / build / release

.PARAMETER Version
    release 时用的版本号，例如 1.26.28

.EXAMPLE
    .\dev.ps1 setup
    .\dev.ps1 check
#>
[CmdletBinding()]
param(
  [ValidateSet("setup", "check", "test", "lint", "format", "type", "run", "build", "release")]
  [string]$Task = "check",
  [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Repo "backend\.venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$BuildPython = Join-Path $Repo "packaging\.build\venv\Scripts\python.exe"

function Say([string]$Message) {
  Write-Host "==> $Message" -ForegroundColor Cyan
}

function Fail([string]$Message) {
  Write-Host "!!! $Message" -ForegroundColor Red
  exit 1
}

function Have([string]$Path) { return (Test-Path $Path) }

function Invoke-Step([string]$Label, [scriptblock]$Action) {
  Say $Label
  & $Action
  if ($LASTEXITCODE -ne 0) { Fail "$Label 失败（退出码 $LASTEXITCODE）" }
}

function Get-Python {
  if (Have $VenvPython) { return $VenvPython }
  if (Have $BuildPython) { return $BuildPython }
  Fail "没找到 Python。先跑：.\dev.ps1 setup"
}

switch ($Task) {
  "setup" {
    Say "装 Python 开发环境（backend\.venv）"
    if (-not (Have $VenvPython)) {
      Invoke-Step "创建虚拟环境" { & python -m venv (Join-Path $Repo "backend\.venv") }
    }
    Invoke-Step "装运行期依赖" { & $VenvPython -m pip install --disable-pip-version-check -q -r (Join-Path $Repo "backend\requirements.lock.txt") }
    Invoke-Step "装开发期工具" { & $VenvPython -m pip install --disable-pip-version-check -q -r (Join-Path $Repo "backend\requirements-dev.txt") }
    if (Get-Command npm -ErrorAction SilentlyContinue) {
      Invoke-Step "装前端工具（eslint / prettier）" { & npm install --no-audit --no-fund --prefix $Repo }
    } else {
      Say "没找到 npm —— 前端检查这步跳过（运行期不需要它）"
    }
    Say "好了。开发命令：.\dev.ps1 check"
  }

  "lint" {
    $python = Get-Python
    Invoke-Step "ruff（代码规范）" { & $python -m ruff check (Join-Path $Repo "backend") (Join-Path $Repo "tools") }
    Invoke-Step "ruff（格式）" { & $python -m ruff format --check (Join-Path $Repo "backend") (Join-Path $Repo "tools") }
    if (Get-Command npm -ErrorAction SilentlyContinue) {
      Invoke-Step "eslint（前端规范）" { & npm run --prefix $Repo lint }
    }
  }

  "format" {
    $python = Get-Python
    Invoke-Step "ruff 自动修" { & $python -m ruff check --fix (Join-Path $Repo "backend") (Join-Path $Repo "tools") }
    Invoke-Step "ruff 格式化" { & $python -m ruff format (Join-Path $Repo "backend") (Join-Path $Repo "tools") }
    if (Get-Command npm -ErrorAction SilentlyContinue) {
      Invoke-Step "prettier 格式化" { & npm run --prefix $Repo format }
      Invoke-Step "eslint 自动修" { & npm run --prefix $Repo lint:fix }
    }
    Say "改完了。跑一次测试确认没改坏：.\dev.ps1 test"
  }

  "type" {
    $python = Get-Python
    Invoke-Step "mypy（类型检查）" { & $python -m mypy --config-file (Join-Path $Repo "pyproject.toml") (Join-Path $Repo "backend\core") }
  }

  "test" {
    $python = Get-Python
    Invoke-Step "后台测试" { & $python (Join-Path $Repo "backend\manage.py") test core }
    Invoke-Step "前端测试" { & node --test (Join-Path $Repo "frontend\test_*.js") }
    Invoke-Step "桌面外壳测试" {
      Push-Location $Repo
      try { & $python -m unittest test_launcher test_backup test_app_window test_credential_dialog test_packaging test_assistant_setup test_create_shortcut } finally { Pop-Location }
    }
  }

  "check" {
    & $MyInvocation.MyCommand.Path -Task lint
    & $MyInvocation.MyCommand.Path -Task type
    & $MyInvocation.MyCommand.Path -Task test
    Say "全过了。"
  }

  "run" {
    $python = Get-Python
    Say "起本地开发实例（数据放在 backend\data，不碰真数据）"
    & $python (Join-Path $Repo "backend\manage.py") runserver 127.0.0.1:8768
  }

  "build" {
    if (-not $Version) { $Version = (Select-String -Path (Join-Path $Repo "backend\core\version.py") -Pattern 'APP_VERSION = "(.*)"').Matches[0].Groups[1].Value }
    Invoke-Step "打包 $Version" { & powershell -NoProfile -File (Join-Path $Repo "packaging\build.ps1") -Version $Version }
  }

  "release" {
    if (-not $Version) { Fail "发版要带版本号：.\dev.ps1 release -Version 1.26.28" }
    & $MyInvocation.MyCommand.Path -Task check
    & $MyInvocation.MyCommand.Path -Task build -Version $Version
    Say "打包好了。剩下的（提交、打标签、发 Release）见 docs\开发环境.md"
  }
}
