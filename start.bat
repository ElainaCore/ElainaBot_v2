@echo off
setlocal
cd /d "%~dp0"
set "ELAINABOT_WINDOWS_LAUNCHER=%~f0"
set "ELAINABOT_ROOT=%~dp0"
set "ELAINABOT_FIRST_ARGUMENT=%~1"
set "ELAINABOT_SECOND_ARGUMENT=%~2"
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -OutputFormat Text -EncodedCommand JABsAD0ARwBlAHQALQBDAG8AbgB0AGUAbgB0ACAALQBMAGkAdABlAHIAYQBsAFAAYQB0AGgAIAAkAGUAbgB2ADoARQBMAEEASQBOAEEAQgBPAFQAXwBXAEkATgBEAE8AVwBTAF8ATABBAFUATgBDAEgARQBSACAALQBFAG4AYwBvAGQAaQBuAGcAIABVAFQARgA4ADsAJABtAD0AWwBhAHIAcgBhAHkAXQA6ADoASQBuAGQAZQB4AE8AZgAoACQAbAAsACcAIwAgAIVRTF0gAFAAbwB3AGUAcgBTAGgAZQBsAGwAIADjTgF4JwApADsAaQBmACgAJABtAC0AbAB0ADAAKQB7AHQAaAByAG8AdwAgACcAKmd+YjBShVFMXYR2IABQAG8AdwBlAHIAUwBoAGUAbABsACAA404BeAIwJwB9ADsAJABwAD0AJABsAFsAKAAkAG0AKwAxACkALgAuACgAJABsAC4AQwBvAHUAbgB0AC0AMQApAF0ALQBqAG8AaQBuAFsARQBuAHYAaQByAG8AbgBtAGUAbgB0AF0AOgA6AE4AZQB3AEwAaQBuAGUAOwAmACgAWwBzAGMAcgBpAHAAdABiAGwAbwBjAGsAXQA6ADoAQwByAGUAYQB0AGUAKAAkAHAAKQApAA==
set "ELAINABOT_EXIT_CODE=%ERRORLEVEL%"
exit /b %ELAINABOT_EXIT_CODE%

# 内嵌 PowerShell 代码
$Utf8Encoding = New-Object Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8Encoding
[Console]::OutputEncoding = $Utf8Encoding
$OutputEncoding = $Utf8Encoding
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Write-ConsoleLine {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ConsoleColor]$Color = [Console]::ForegroundColor
    )

    if ([Console]::IsOutputRedirected) {
        [Console]::WriteLine($Message)
        return
    }

    $previousColor = [Console]::ForegroundColor
    try {
        [Console]::ForegroundColor = $Color
        [Console]::WriteLine($Message)
    } finally {
        [Console]::ForegroundColor = $previousColor
    }
}

$SetupOnly = $false
$FirstArgument = [string]$env:ELAINABOT_FIRST_ARGUMENT
$SecondArgument = [string]$env:ELAINABOT_SECOND_ARGUMENT

if ($SecondArgument) {
    Write-ConsoleLine '[ElainaBot] 错误：不支持多个启动参数。' Red
    exit 2
}

switch ($FirstArgument.ToLowerInvariant()) {
    '' { }
    '-setuponly' { $SetupOnly = $true }
    '--setup-only' { $SetupOnly = $true }
    '-h' {
        Write-ConsoleLine '用法：start.bat [-SetupOnly]'
        exit 0
    }
    '--help' {
        Write-ConsoleLine '用法：start.bat [-SetupOnly]'
        exit 0
    }
    default {
        Write-ConsoleLine "[ElainaBot] 错误：未知参数：$FirstArgument" Red
        exit 2
    }
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$BootstrapVersion = '5'
$PythonVersion = '3.13'
$DefaultPythonInstallMirror = 'https://registry.npmmirror.com/-/binary/python-build-standalone'
$PythonInstallMirror = if (-not [string]::IsNullOrWhiteSpace($env:ELAINABOT_PYTHON_MIRROR)) {
    $env:ELAINABOT_PYTHON_MIRROR.Trim().TrimEnd('/')
} else {
    $DefaultPythonInstallMirror
}
$PipMirror = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$OfficialPipSource = 'https://pypi.org/simple'
$WebPanelPackage = 'pywebview>=6.2,<7'
$RootDir = [IO.Path]::GetFullPath($env:ELAINABOT_ROOT)
$VenvDir = Join-Path $RootDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$ToolsDir = Join-Path $RootDir '.bootstrap\uv'
$StampFile = Join-Path $VenvDir '.elainabot-requirements.sha256'
Set-Location $RootDir

function Write-Step {
    param([string]$Message)
    Write-ConsoleLine "[ElainaBot] $Message" Cyan
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    & $FilePath @Arguments | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "命令执行失败，退出代码 ${LASTEXITCODE}：$FilePath $($Arguments -join ' ')"
    }
}

function Invoke-PipInstall {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    Write-Step '正在优先使用清华 PyPI 镜像安装依赖...'
    & $VenvPython -m pip install --disable-pip-version-check --index-url $PipMirror @Arguments | Out-Host
    if ($LASTEXITCODE -eq 0) {
        return
    }

    Write-Step '镜像源安装失败，正在切换到官方 PyPI...'
    Invoke-Checked $VenvPython (@(
        '-m', 'pip', 'install', '--disable-pip-version-check',
        '--index-url', $OfficialPipSource
    ) + $Arguments)
}

function Get-CommandPath {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        return $null
    }
    return $command.Source
}

function Test-PythonCandidate {
    param(
        [string]$FilePath,
        [string[]]$BaseArguments = @(),
        [string]$RequiredVersion = ''
    )

    if (-not $FilePath) {
        return $null
    }
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $versionCheck = "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    if ($RequiredVersion) {
        $parts = $RequiredVersion.Split('.')
        $versionCheck = "import sys; raise SystemExit(0 if sys.version_info[:2] == ($($parts[0]), $($parts[1])) else 1)"
    }
    try {
        & $FilePath @BaseArguments -c $versionCheck *> $null
        $probeExitCode = $LASTEXITCODE
    } catch {
        $probeExitCode = 1
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($probeExitCode -ne 0) {
        return $null
    }
    $version = (& $FilePath @BaseArguments -c "import platform; print(platform.python_version())").Trim()
    return [PSCustomObject]@{
        FilePath = $FilePath
        Arguments = $BaseArguments
        Version = $version
    }
}

function Find-PreferredPython {
    $pyLauncher = Get-CommandPath 'py'
    if ($pyLauncher) {
        $candidate = Test-PythonCandidate -FilePath $pyLauncher -BaseArguments @('-3.13') -RequiredVersion $PythonVersion
        if ($candidate) {
            return $candidate
        }
    }

    foreach ($name in @('python3.13', 'python3', 'python')) {
        $path = Get-CommandPath $name
        $candidate = Test-PythonCandidate -FilePath $path -RequiredVersion $PythonVersion
        if ($candidate) {
            return $candidate
        }
    }
    return $null
}

function Test-UvSupportsPythonMirror {
    param([Parameter(Mandatory = $true)][string]$UvPath)

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $helpOutput = (& $UvPath python install --help 2>&1 | Out-String)
        $helpExitCode = $LASTEXITCODE
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return $helpExitCode -eq 0 -and $helpOutput -match '(?m)^\s*--mirror\s'
}

function Ensure-Uv {
    $uvPath = Get-CommandPath 'uv'
    if ($uvPath -and (Test-UvSupportsPythonMirror -UvPath $uvPath)) {
        return $uvPath
    }

    $localUv = Join-Path $ToolsDir 'uv.exe'
    if ((Test-Path -LiteralPath $localUv) -and (Test-UvSupportsPythonMirror -UvPath $localUv)) {
        return $localUv
    }

    if ($uvPath -or (Test-Path -LiteralPath $localUv)) {
        Write-Step '现有 Python 环境引导工具版本过旧，正在更新项目专用版本...'
    } else {
        Write-Step '正在安装项目专用的 Python 环境引导工具...'
    }
    New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null
    $installerPath = Join-Path ([IO.Path]::GetTempPath()) 'elainabot-uv-install.ps1'
    try {
        Invoke-WebRequest -Uri 'https://astral.sh/uv/install.ps1' -OutFile $installerPath
        $env:UV_INSTALL_DIR = $ToolsDir
        $env:UV_NO_MODIFY_PATH = '1'
        Invoke-Checked 'powershell.exe' @(
            '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $installerPath
        )
    } finally {
        Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path -LiteralPath $localUv)) {
        throw '无法安装项目专用的 Python 环境引导工具。'
    }
    if (-not (Test-UvSupportsPythonMirror -UvPath $localUv)) {
        throw '项目专用的 Python 环境引导工具不支持镜像下载，请稍后重试。'
    }
    return $localUv
}

function Install-ManagedPython {
    param([Parameter(Mandatory = $true)][string]$UvPath)

    $hadNoProgress = Test-Path Env:UV_NO_PROGRESS
    $previousNoProgress = if ($hadNoProgress) {
        [Environment]::GetEnvironmentVariable('UV_NO_PROGRESS', 'Process')
    } else {
        $null
    }
    try {
        Remove-Item Env:UV_NO_PROGRESS -ErrorAction SilentlyContinue
        Write-Step "[1/6] 正在通过镜像下载项目专用的 Python $PythonVersion（将显示实时进度）..."
        & $UvPath --color always python install --no-bin --no-registry --mirror $PythonInstallMirror $PythonVersion | Out-Host
        if ($LASTEXITCODE -eq 0) {
            return
        }

        Write-Step 'Python 镜像下载失败，正在切换到官方源...'
        & $UvPath --color always python install --no-bin --no-registry $PythonVersion | Out-Host
        if ($LASTEXITCODE -ne 0) {
            throw "Python $PythonVersion 下载失败，镜像源和官方源均不可用。"
        }
    } finally {
        if ($hadNoProgress) {
            $env:UV_NO_PROGRESS = $previousNoProgress
        } else {
            Remove-Item Env:UV_NO_PROGRESS -ErrorAction SilentlyContinue
        }
    }
}

function Backup-InvalidVenv {
    if (-not (Test-Path -LiteralPath $VenvDir)) {
        return
    }
    $backupName = ".venv.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Write-Step "现有虚拟环境无效，正在将其移动到 $backupName。"
    Move-Item -LiteralPath $VenvDir -Destination (Join-Path $RootDir $backupName)
}

function Ensure-VirtualEnvironment {
    Write-Step '[1/6] 正在检查 Python 3.11 或更高版本...'
    if (Test-Path -LiteralPath $VenvPython) {
        $existing = Test-PythonCandidate $VenvPython
        if ($existing) {
            Write-Step "[1/6] Python 已就绪：$($existing.Version)"
            Write-Step '[2/6] 已有虚拟环境可用：.venv'
            return
        }
    }

    Backup-InvalidVenv
    $python = Find-PreferredPython
    if ($python) {
        Write-Step "[1/6] 已找到 Python 3.13：$($python.Version)"
        Write-Step '[2/6] 正在创建项目虚拟环境：.venv...'
        & $python.FilePath @($python.Arguments) -m venv $VenvDir
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $VenvPython)) {
            Write-Step '[2/6] 虚拟环境创建成功。'
            return
        }
        if (Test-Path -LiteralPath $VenvDir) {
            $failedName = ".venv.failed-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Move-Item -LiteralPath $VenvDir -Destination (Join-Path $RootDir $failedName)
        }
        Write-Step '系统 Python 无法创建虚拟环境，将改用项目专用的 Python。'
    }

    $uvPath = Ensure-Uv
    Install-ManagedPython -UvPath $uvPath
    Write-Step '[2/6] 正在创建项目虚拟环境：.venv...'
    Invoke-Checked $uvPath @(
        'venv', '--python', $PythonVersion, '--managed-python', '--no-python-downloads', $VenvDir
    )
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw '虚拟环境创建结束，但未找到可用的 python.exe。'
    }
    Write-Step '[2/6] 虚拟环境创建成功。'
}

function Get-RequirementFiles {
    $files = @()
    $files += Get-ChildItem -LiteralPath $RootDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq 'requirements.txt' -or $_.Name -like '*_requirements.txt' }
    foreach ($directory in @('modules', 'plugins')) {
        $path = Join-Path $RootDir $directory
        if (Test-Path -LiteralPath $path) {
            $files += Get-ChildItem -LiteralPath $path -Recurse -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -eq 'requirements.txt' -or $_.Name -like '*_requirements.txt' }
        }
    }
    return @($files | Sort-Object FullName -Unique)
}

function Get-RequirementsFingerprint {
    param([System.IO.FileInfo[]]$Files)

    $builder = New-Object System.Text.StringBuilder
    [void]$builder.AppendLine("bootstrap=$BootstrapVersion")
    foreach ($file in $Files) {
        $relativePath = $file.FullName.Substring($RootDir.Length).TrimStart('\', '/')
        $fileHasher = [Security.Cryptography.SHA256]::Create()
        try {
            $fileBytes = [IO.File]::ReadAllBytes($file.FullName)
            $fileHash = ([BitConverter]::ToString($fileHasher.ComputeHash($fileBytes))).Replace('-', '').ToLowerInvariant()
        } finally {
            $fileHasher.Dispose()
        }
        [void]$builder.AppendLine("$relativePath=$fileHash")
    }

    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($builder.ToString())
        return ([BitConverter]::ToString($sha256.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
}

function Test-CoreDependencies {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $VenvPython -c "import aiohttp, cryptography, dotenv, httpx, psutil, qcloud_cos, websockets, yaml" *> $null
        $importExitCode = $LASTEXITCODE
    } catch {
        $importExitCode = 1
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return $importExitCode -eq 0
}

function Ensure-Dependencies {
    Write-Step '[3/6] 正在扫描框架、模块和插件的依赖文件...'
    $requirements = @(Get-RequirementFiles)
    if ($requirements.Count -eq 0) {
        throw '未找到任何依赖文件。'
    }
    Write-Step "[3/6] 已找到 $($requirements.Count) 个依赖文件。"

    $fingerprint = Get-RequirementsFingerprint $requirements
    $savedFingerprint = if (Test-Path -LiteralPath $StampFile) {
        (Get-Content -LiteralPath $StampFile -Raw).Trim()
    } else {
        ''
    }

    if ($savedFingerprint -eq $fingerprint -and (Test-CoreDependencies)) {
        Write-Step '[4/6] 框架依赖已经安装且为最新状态，无需重复安装。'
        return
    }

    Write-Step "[4/6] 正在根据 $($requirements.Count) 个依赖文件安装框架依赖..."
    & $VenvPython -m ensurepip --upgrade 2>$null
    Invoke-PipInstall -Arguments @('--upgrade', 'pip', 'setuptools', 'wheel')

    $arguments = @()
    foreach ($requirement in $requirements) {
        $arguments += @('-r', $requirement.FullName)
    }
    Invoke-PipInstall -Arguments $arguments

    if (-not (Test-CoreDependencies)) {
        throw '依赖安装已经结束，但仍有一个或多个核心包无法导入。'
    }
    Set-Content -LiteralPath $StampFile -Value $fingerprint -Encoding ASCII
    Write-Step '[4/6] 框架依赖安装完成并通过验证。'
}

function Test-WebPanelDependency {
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $VenvPython -c "import webview" *> $null
        $importExitCode = $LASTEXITCODE
    } catch {
        $importExitCode = 1
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    return $importExitCode -eq 0
}

function Ensure-WebPanelDependency {
    Write-Step '[5/6] 正在检查 Windows 桌面窗口组件...'
    if (Test-WebPanelDependency) {
        Write-Step '[5/6] Windows 桌面窗口组件已经安装，无需重复安装。'
        return
    }

    Write-Step '[5/6] 正在安装启动脚本专用的 Windows 桌面窗口组件...'
    & $VenvPython -m ensurepip --upgrade 2>$null
    Invoke-PipInstall -Arguments @($WebPanelPackage)
    if (-not (Test-WebPanelDependency)) {
        throw 'Windows 桌面窗口组件安装结束，但 pywebview 仍无法导入。'
    }
    Write-Step '[5/6] Windows 桌面窗口组件安装完成。'
}

function Get-ConfiguredWebPort {
    $readerSource = @'
import os
import sys

from core.base.config import cfg

config_dir = os.path.join(sys.argv[1], 'config')
cfg.init(config_dir)
value = cfg.get('settings', 'server.port', 5200)
try:
    port = int(value)
except (TypeError, ValueError):
    raise SystemExit('配置项 server.port 必须是整数。')
if not 1 <= port <= 65535:
    raise SystemExit('配置项 server.port 必须在 1 到 65535 之间。')
print(port)
'@

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $portOutput = @(& $VenvPython -c $readerSource $RootDir 2>&1)
        $portExitCode = $LASTEXITCODE
    } catch {
        $portOutput = @($_.Exception.Message)
        $portExitCode = 1
    } finally {
        $ErrorActionPreference = $previousPreference
    }

    if ($portExitCode -ne 0) {
        $details = ($portOutput | ForEach-Object { $_.ToString() }) -join ' '
        throw "无法读取 Web 管理面板端口：$details"
    }

    [int]$port = 0
    $portText = ($portOutput | Select-Object -Last 1).ToString().Trim()
    if (-not [int]::TryParse($portText, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw "读取到无效的 Web 管理面板端口：$portText"
    }
    return $port
}

function Test-LocalPortOpen {
    param([Parameter(Mandatory = $true)][int]$Port)

    foreach ($address in @('127.0.0.1', '::1')) {
        $client = New-Object Net.Sockets.TcpClient
        try {
            $connection = $client.ConnectAsync($address, $Port)
            if ($connection.Wait(800) -and $client.Connected) {
                return $true
            }
        } catch {
        } finally {
            $client.Dispose()
        }
    }
    return $false
}

function Test-WebPanelAvailable {
    param([Parameter(Mandatory = $true)][string]$Url)

    $response = $null
    try {
        $request = [Net.HttpWebRequest]::Create($Url)
        $request.Proxy = $null
        $request.Method = 'GET'
        $request.AllowAutoRedirect = $true
        $request.Timeout = 3000
        $request.ReadWriteTimeout = 3000
        $response = $request.GetResponse()
        $statusCode = [int]$response.StatusCode
        return $statusCode -ge 200 -and $statusCode -lt 400
    } catch {
        return $false
    } finally {
        if ($response) {
            $response.Close()
        }
    }
}

function Start-WebPanelWindow {
    param([Parameter(Mandatory = $true)][string]$Url)

    $windowSource = @'
import sys
import time
import urllib.request

import webview

panel_url = sys.argv[1]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(120):
    try:
        with opener.open(panel_url, timeout=2):
            pass
        break
    except Exception:
        time.sleep(1)
else:
    raise SystemExit(1)

webview.create_window(
    'ElainaBot 管理面板',
    panel_url,
    width=1280,
    height=820,
    min_size=(960, 640),
)
webview.start()
'@

    $encodedSource = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($windowSource))
    $launcherSource = "import base64;exec(base64.b64decode('$encodedSource'))"
    $arguments = @('-c', "`"$launcherSource`"", "`"$Url`"")
    $windowPython = Join-Path $VenvDir 'Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $windowPython)) {
        $windowPython = $VenvPython
    }
    return Start-Process -FilePath $windowPython -ArgumentList $arguments -PassThru
}

try {
    Write-Step '正在准备运行环境...'
    Ensure-VirtualEnvironment
    Ensure-Dependencies
    Ensure-WebPanelDependency

    if ($SetupOnly) {
        Write-Step '[6/6] 已选择仅配置环境模式，跳过框架启动。'
        Write-Step '运行环境配置成功。'
        exit 0
    }

    $panelPort = Get-ConfiguredWebPort
    $panelUrl = "http://localhost:${panelPort}/web/"
    Write-Step "Web 管理面板：$panelUrl"
    Write-Step "[6/6] 正在检查配置端口 $panelPort 是否已经开启..."
    if (Test-LocalPortOpen -Port $panelPort) {
        if (-not (Test-WebPanelAvailable -Url $panelUrl)) {
            throw "配置端口 $panelPort 已被占用，但未检测到 ElainaBot 管理面板。请检查端口占用情况。"
        }
        Write-Step "[6/6] 检测到框架已经运行，仅重新打开桌面面板窗口。"
        $existingPanelWindow = Start-WebPanelWindow -Url $panelUrl
        if ($existingPanelWindow) {
            $existingPanelWindow.Dispose()
        }
        Write-Step '桌面面板窗口已打开，无需重新启动框架。'
        exit 0
    }

    Write-Step "[6/6] 配置端口 $panelPort 尚未开启，正在启动 ElainaBot 框架..."
    Write-Step '面板就绪后将自动打开 ElainaBot 桌面窗口。'
    $panelWindow = Start-WebPanelWindow -Url $panelUrl
    try {
        & $VenvPython (Join-Path $RootDir 'main.py')
        $frameworkExitCode = $LASTEXITCODE
    } finally {
        if ($panelWindow -and -not $panelWindow.HasExited) {
            Stop-Process -Id $panelWindow.Id -Force -ErrorAction SilentlyContinue
        }
        if ($panelWindow) {
            $panelWindow.Dispose()
        }
    }
    exit $frameworkExitCode
} catch {
    Write-ConsoleLine "[ElainaBot] 错误：$($_.Exception.Message)" Red
    exit 1
}
