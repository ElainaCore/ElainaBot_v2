@echo off
setlocal
cd /d "%~dp0"
set "ELAINABOT_WINDOWS_LAUNCHER=%~f0"
set "ELAINABOT_ROOT=%~dp0"
set "ELAINABOT_FIRST_ARGUMENT=%~1"
set "ELAINABOT_SECOND_ARGUMENT=%~2"
chcp 65001 >nul
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -OutputFormat Text -EncodedCommand JABsAD0ARwBlAHQALQBDAG8AbgB0AGUAbgB0ACAALQBMAGkAdABlAHIAYQBsAFAAYQB0AGgAIAAkAGUAbgB2ADoARQBMAEEASQBOAEEAQgBPAFQAXwBXAEkATgBEAE8AVwBTAF8ATABBAFUATgBDAEgARQBSACAALQBFAG4AYwBvAGQAaQBuAGcAIABVAFQARgA4ADsAJABtAD0AWwBhAHIAcgBhAHkAXQA6ADoASQBuAGQAZQB4AE8AZgAoACQAbAAsACcAIwAgAIVRTF0gAFAAbwB3AGUAcgBTAGgAZQBsAGwAIADjTgF4JwApADsAaQBmACgAJABtAC0AbAB0ADAAKQB7AHQAaAByAG8AdwAgACcAKmd+YjBShVFMXYR2IABQAG8AdwBlAHIAUwBoAGUAbABsACAA404BeAIwJwB9ADsAJABwAD0AJABsAFsAKAAkAG0AKwAxACkALgAuACgAJABsAC4AQwBvAHUAbgB0AC0AMQApAF0ALQBqAG8AaQBuAFsARQBuAHYAaQByAG8AbgBtAGUAbgB0AF0AOgA6AE4AZQB3AEwAaQBuAGUAOwAmACgAWwBzAGMAcgBpAHAAdABiAGwAbwBjAGsAXQA6ADoAQwByAGUAYQB0AGUAKAAkAHAAKQApAA==
set "ELAINABOT_EXIT_CODE=%ERRORLEVEL%"
exit /b %ELAINABOT_EXIT_CODE%

# 内嵌 PowerShell 代码
$SetupOnly = $false
$FirstArgument = [string]$env:ELAINABOT_FIRST_ARGUMENT
$SecondArgument = [string]$env:ELAINABOT_SECOND_ARGUMENT

if ($SecondArgument) {
    Write-Host '[ElainaBot] 错误：不支持多个启动参数。' -ForegroundColor Red
    exit 2
}

switch ($FirstArgument.ToLowerInvariant()) {
    '' { }
    '-setuponly' { $SetupOnly = $true }
    '--setup-only' { $SetupOnly = $true }
    '-h' {
        Write-Host '用法：start.bat [-SetupOnly]'
        exit 0
    }
    '--help' {
        Write-Host '用法：start.bat [-SetupOnly]'
        exit 0
    }
    default {
        Write-Host "[ElainaBot] 错误：未知参数：$FirstArgument" -ForegroundColor Red
        exit 2
    }
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$BootstrapVersion = '4'
$PythonVersion = '3.13'
$PipMirror = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$OfficialPipSource = 'https://pypi.org/simple'
$RootDir = [IO.Path]::GetFullPath($env:ELAINABOT_ROOT)
$VenvDir = Join-Path $RootDir '.venv'
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
$ToolsDir = Join-Path $RootDir '.bootstrap\uv'
$StampFile = Join-Path $VenvDir '.elainabot-requirements.sha256'
Set-Location $RootDir

function Write-Step {
    param([string]$Message)
    Write-Host "[ElainaBot] $Message" -ForegroundColor Cyan
}

function Refresh-ProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = (@($env:Path, $userPath, $machinePath) | Where-Object { $_ }) -join ';'
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

function Install-PythonWithWinget {
    $wingetPath = Get-CommandPath 'winget'
    if (-not $wingetPath) {
        return $null
    }

Write-Step '未找到 Python 3.13，正在安装 Python 3.13...'
    Invoke-Checked $wingetPath @(
        'install', '--id', 'Python.Python.3.13', '--exact', '--source', 'winget',
        '--scope', 'user', '--accept-package-agreements', '--accept-source-agreements', '--silent'
    )
    Refresh-ProcessPath
return Find-PreferredPython
}

function Ensure-Uv {
    $uvPath = Get-CommandPath 'uv'
    if ($uvPath) {
        return $uvPath
    }

    $localUv = Join-Path $ToolsDir 'uv.exe'
    if (Test-Path -LiteralPath $localUv) {
        return $localUv
    }

    Write-Step '正在安装项目专用的 Python 环境引导工具...'
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
    return $localUv
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
    Write-Step '[1/5] 正在检查 Python 3.11 或更高版本...'
    if (Test-Path -LiteralPath $VenvPython) {
        $existing = Test-PythonCandidate $VenvPython
        if ($existing) {
            Write-Step "[1/5] Python 已就绪：$($existing.Version)"
            Write-Step '[2/5] 已有虚拟环境可用：.venv'
            return
        }
    }

    Backup-InvalidVenv
$python = Find-PreferredPython
    if (-not $python) {
        try {
            $python = Install-PythonWithWinget
        } catch {
            Write-Step 'winget 无法安装 Python 3.13，将改用项目专用的 Python。'
            $python = $null
        }
    }

    if ($python) {
Write-Step "[1/5] 已找到 Python 3.13：$($python.Version)"
        Write-Step '[2/5] 正在创建项目虚拟环境：.venv...'
        & $python.FilePath @($python.Arguments) -m venv $VenvDir
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $VenvPython)) {
            Write-Step '[2/5] 虚拟环境创建成功。'
            return
        }
        if (Test-Path -LiteralPath $VenvDir) {
            $failedName = ".venv.failed-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            Move-Item -LiteralPath $VenvDir -Destination (Join-Path $RootDir $failedName)
        }
        Write-Step '系统 Python 无法创建虚拟环境，将改用项目专用的 Python。'
    }

    $uvPath = Ensure-Uv
    Write-Step "[1/5] 正在下载项目专用的 Python $PythonVersion..."
    Write-Step '[2/5] 正在创建项目虚拟环境：.venv...'
    Invoke-Checked $uvPath @('venv', '--python', $PythonVersion, $VenvDir)
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        throw '虚拟环境创建结束，但未找到可用的 python.exe。'
    }
    Write-Step '[2/5] 虚拟环境创建成功。'
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
    & $VenvPython -c "import aiohttp, cryptography, dotenv, httpx, psutil, qcloud_cos, websockets, yaml" 2>$null
    return $LASTEXITCODE -eq 0
}

function Ensure-Dependencies {
    Write-Step '[3/5] 正在扫描框架、模块和插件的依赖文件...'
    $requirements = @(Get-RequirementFiles)
    if ($requirements.Count -eq 0) {
        throw '未找到任何依赖文件。'
    }
    Write-Step "[3/5] 已找到 $($requirements.Count) 个依赖文件。"

    $fingerprint = Get-RequirementsFingerprint $requirements
    $savedFingerprint = if (Test-Path -LiteralPath $StampFile) {
        (Get-Content -LiteralPath $StampFile -Raw).Trim()
    } else {
        ''
    }

    if ($savedFingerprint -eq $fingerprint -and (Test-CoreDependencies)) {
        Write-Step '[4/5] 依赖已经安装且为最新状态，无需重复安装。'
        return
    }

    Write-Step "[4/5] 正在根据 $($requirements.Count) 个依赖文件安装依赖..."
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
    Write-Step '[4/5] 依赖安装完成并通过验证。'
}

try {
    Write-Step '正在准备运行环境...'
    Ensure-VirtualEnvironment
    Ensure-Dependencies

    if ($SetupOnly) {
        Write-Step '[5/5] 已选择仅配置环境模式，跳过框架启动。'
        Write-Step '运行环境配置成功。'
        exit 0
    }

    Write-Step '[5/5] 正在启动 ElainaBot 框架...'
    Write-Step 'Web 管理面板：http://localhost:5200/web/'
    & $VenvPython (Join-Path $RootDir 'main.py')
    exit $LASTEXITCODE
} catch {
    Write-Host "[ElainaBot] 错误：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
