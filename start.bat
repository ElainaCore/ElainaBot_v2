@echo off
@setlocal
@cd /d "%~dp0"
@set "ELAINABOT_WINDOWS_LAUNCHER=%~f0"
@set "ELAINABOT_ROOT=%~dp0"
@set "ELAINABOT_FIRST_ARGUMENT=%~1"
@set "ELAINABOT_SECOND_ARGUMENT=%~2"
@set "PYTHONUTF8=1"
@set "PYTHONIOENCODING=utf-8"
@powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -OutputFormat Text -EncodedCommand JABsAD0ARwBlAHQALQBDAG8AbgB0AGUAbgB0ACAALQBMAGkAdABlAHIAYQBsAFAAYQB0AGgAIAAkAGUAbgB2ADoARQBMAEEASQBOAEEAQgBPAFQAXwBXAEkATgBEAE8AVwBTAF8ATABBAFUATgBDAEgARQBSACAALQBFAG4AYwBvAGQAaQBuAGcAIABVAFQARgA4ADsAJABtAD0AWwBhAHIAcgBhAHkAXQA6ADoASQBuAGQAZQB4AE8AZgAoACQAbAAsACcAIwAgAIVRTF0gAFAAbwB3AGUAcgBTAGgAZQBsAGwAIADjTgF4JwApADsAaQBmACgAJABtAC0AbAB0ADAAKQB7AHQAaAByAG8AdwAgACcAKmd+YjBShVFMXYR2IABQAG8AdwBlAHIAUwBoAGUAbABsACAA404BeAIwJwB9ADsAJABwAD0AJABsAFsAKAAkAG0AKwAxACkALgAuACgAJABsAC4AQwBvAHUAbgB0AC0AMQApAF0ALQBqAG8AaQBuAFsARQBuAHYAaQByAG8AbgBtAGUAbgB0AF0AOgA6AE4AZQB3AEwAaQBuAGUAOwAmACgAWwBzAGMAcgBpAHAAdABiAGwAbwBjAGsAXQA6ADoAQwByAGUAYQB0AGUAKAAkAHAAKQApAA==
@set "ELAINABOT_EXIT_CODE=%ERRORLEVEL%"
@if not "%ELAINABOT_EXIT_CODE%"=="0" (
    @echo(
    @echo [ElainaBot] Startup failed with exit code %ELAINABOT_EXIT_CODE%.
    @echo [ElainaBot] Review the error above, then press any key to close this window...
    @pause >nul
) else (
    @echo(
    @echo [ElainaBot] Startup completed. Press any key to close this window...
    @pause >nul
)
@exit /b %ELAINABOT_EXIT_CODE%

# 内嵌 PowerShell 代码
$Utf8Encoding = New-Object Text.UTF8Encoding($false)
$WindowsVersion = [Environment]::OSVersion.Version
$UseLegacyWindowsPath = $WindowsVersion.Major -lt 10
$UseSystemBrowserPanel = $UseLegacyWindowsPath

function Test-RichConsoleOutputAvailable {
    if ($UseLegacyWindowsPath -or
        [string]$env:ELAINABOT_RICH_OUTPUT -eq '0' -or
        [Console]::IsOutputRedirected) {
        return $false
    }

    try {
        if (-not ('ElainaBot.NativeConsole' -as [type])) {
            Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace ElainaBot {
    public static class NativeConsole {
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern IntPtr GetStdHandle(int standardHandle);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool GetConsoleMode(IntPtr consoleHandle, out uint mode);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool SetConsoleMode(IntPtr consoleHandle, uint mode);
    }
}
'@ -ErrorAction Stop
        }

        $stdoutHandle = [ElainaBot.NativeConsole]::GetStdHandle(-11)
        if ($stdoutHandle -eq [IntPtr]::Zero -or $stdoutHandle -eq [IntPtr]::MinusOne) {
            return $false
        }

        [uint32]$consoleMode = 0
        if (-not [ElainaBot.NativeConsole]::GetConsoleMode($stdoutHandle, [ref]$consoleMode)) {
            return $false
        }

        $enableVirtualTerminalProcessing = [uint32]0x0004
        if (($consoleMode -band $enableVirtualTerminalProcessing) -eq 0) {
            if (-not [ElainaBot.NativeConsole]::SetConsoleMode(
                $stdoutHandle,
                ($consoleMode -bor $enableVirtualTerminalProcessing)
            )) {
                return $false
            }
        }
        return $true
    } catch {
        return $false
    }
}

$UseRichConsoleOutput = Test-RichConsoleOutputAvailable
$ProgressPreference = if ($UseRichConsoleOutput) { 'Continue' } else { 'SilentlyContinue' }
if (-not $UseLegacyWindowsPath) {
    [Console]::InputEncoding = $Utf8Encoding
    [Console]::OutputEncoding = $Utf8Encoding
    $OutputEncoding = $Utf8Encoding
}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = if ($UseLegacyWindowsPath) {
    [Console]::OutputEncoding.WebName
} else {
    'utf-8'
}
[Net.ServicePointManager]::SecurityProtocol =
    [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Write-ConsoleLine {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ConsoleColor]$Color = [Console]::ForegroundColor
    )

    try {
        [Console]::Out.WriteLine($Message)
    } catch {
        # Some legacy consoles cannot accept Unicode writes; keep the failure readable.
        $asciiMessage = [Text.RegularExpressions.Regex]::Replace($Message, '[^\x00-\x7F]', '?')
        try { [Console]::Out.WriteLine($asciiMessage) } catch { }
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
$MinimumPythonVersion = '3.11'
$ManagedPythonVersion = '3.13'
$DefaultPythonInstallMirror = 'https://registry.npmmirror.com/-/binary/python'
$PythonInstallMirror = if (-not [string]::IsNullOrWhiteSpace($env:ELAINABOT_PYTHON_MIRROR)) {
    $env:ELAINABOT_PYTHON_MIRROR.Trim().TrimEnd('/')
} else {
    $DefaultPythonInstallMirror
}
$PipMirror = 'https://pypi.tuna.tsinghua.edu.cn/simple'
$OfficialPipSource = 'https://pypi.org/simple'
$FrameworkArchiveUrl = 'https://github.com/ElainaCore/ElainaBot_v2/archive/refs/heads/main.zip'
$FrameworkManualDownloadUrl = 'https://codeload.github.com/ElainaCore/ElainaBot_v2/zip/refs/heads/main'
$FrameworkMirrors = @(
    'https://github.chenc.dev/'
    'https://fastgit.cc/'
    'https://gh.dpik.top/'
    'https://gh.jasonzeng.dev/'
    'https://ghf.xn--eqrr82bzpe.top/'
    'https://gh.xxooo.cf/'
    'https://ghproxy.imciel.com/'
    'https://ghproxy.cxkpro.top/'
    'https://gh.927223.xyz/'
    'https://gitproxy.mrhjx.cn/'
)
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

function Write-DependencyProgress {
    param(
        [Parameter(Mandatory = $true)][ValidateRange(0, 100)][int]$Percent,
        [Parameter(Mandatory = $true)][string]$Activity
    )

    $width = 30
    $filled = [Math]::Min($width, [Math]::Floor($Percent * $width / 100))
    $bar = ('#' * $filled) + ('-' * ($width - $filled))
    Write-ConsoleLine ("[ElainaBot] [5/6] [$bar] {0,3}%  $Activity" -f $Percent)
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

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "命令执行失败，退出代码 ${LASTEXITCODE}：$FilePath $($Arguments -join ' ')"
    }
}

function Invoke-VisibleProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$HeartbeatMessage,
        [bool]$ForwardOutput = $true
    )

    # Redirect child output to temporary files so it can be forwarded line by
    # line while the process runs, including on Windows PowerShell 5.1.
    $token = [Guid]::NewGuid().ToString('N')
    $stdoutPath = Join-Path ([IO.Path]::GetTempPath()) ("elainabot-$token.out")
    $stderrPath = Join-Path ([IO.Path]::GetTempPath()) ("elainabot-$token.err")
    $quotedArguments = foreach ($argument in $Arguments) {
        $text = [string]$argument
        if ($text -match '[\s"]') {
            '"' + $text.Replace('"', '\"') + '"'
        } else {
            $text
        }
    }
    $process = $null
    $stdoutIndex = 0
    $stderrIndex = 0
    $lastHeartbeat = Get-Date
    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList ($quotedArguments -join ' ') -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru -WindowStyle Hidden
        while (-not $process.HasExited) {
            if ($ForwardOutput) {
                foreach ($stream in @(@{ Path = $stdoutPath; Index = [ref]$stdoutIndex; Error = $false }, @{ Path = $stderrPath; Index = [ref]$stderrIndex; Error = $true })) {
                    try {
                        $lines = @(Get-Content -LiteralPath $stream.Path -Encoding UTF8 -ErrorAction SilentlyContinue)
                        while ($stream.Index.Value -lt $lines.Count) {
                            $line = [string]$lines[$stream.Index.Value]
                            $stream.Index.Value++
                            if ($stream.Error) {
                                Write-ConsoleLine ("[pip] $line")
                            } else {
                                Write-ConsoleLine $line
                            }
                        }
                    } catch { }
                }
            }
            if (((Get-Date) - $lastHeartbeat).TotalSeconds -ge 3) {
                Write-Step $HeartbeatMessage
                $lastHeartbeat = Get-Date
            }
            Start-Sleep -Milliseconds 250
        }
        $process.WaitForExit()
        if ($ForwardOutput) {
            foreach ($stream in @(@{ Path = $stdoutPath; Index = [ref]$stdoutIndex; Error = $false }, @{ Path = $stderrPath; Index = [ref]$stderrIndex; Error = $true })) {
                try {
                    $lines = @(Get-Content -LiteralPath $stream.Path -Encoding UTF8 -ErrorAction SilentlyContinue)
                    while ($stream.Index.Value -lt $lines.Count) {
                        $line = [string]$lines[$stream.Index.Value]
                        $stream.Index.Value++
                        if ($stream.Error) { Write-ConsoleLine ("[pip] $line") } else { Write-ConsoleLine $line }
                    }
                } catch { }
            }
        }
        return [int]$process.ExitCode
    } finally {
        if ($process) { $process.Dispose() }
        Remove-Item -LiteralPath $stdoutPath,$stderrPath -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-PipInstall {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $richDisplayArguments = @('--progress-bar', 'on')
    $compatibleDisplayArguments = @('--progress-bar', 'off', '--no-color')
    Write-Step '正在优先使用清华 PyPI 镜像安装依赖...'
    Write-Step 'pip 正在解析、下载并安装依赖，请稍候；过程中会持续显示包名。'

    if ($UseRichConsoleOutput) {
        $pipArguments = @('-u', '-m', 'pip', 'install', '--disable-pip-version-check') + $richDisplayArguments + @('--index-url', $PipMirror) + $Arguments
        & $VenvPython @pipArguments
        $pipExitCode = $LASTEXITCODE
        if ($pipExitCode -ne 0) {
            Write-Step '动态进度模式执行失败，正在使用纯文本兼容模式重试当前镜像...'
            $pipArguments = @('-u', '-m', 'pip', 'install', '--disable-pip-version-check') + $compatibleDisplayArguments + @('--index-url', $PipMirror) + $Arguments
            $pipExitCode = Invoke-VisibleProcess -FilePath $VenvPython -Arguments $pipArguments -HeartbeatMessage 'pip 仍在以兼容模式处理依赖，请耐心等待...'
        }
    } else {
        $pipArguments = @('-u', '-m', 'pip', 'install', '--disable-pip-version-check') + $compatibleDisplayArguments + @('--index-url', $PipMirror) + $Arguments
        $pipExitCode = Invoke-VisibleProcess -FilePath $VenvPython -Arguments $pipArguments -HeartbeatMessage 'pip 仍在处理依赖，请耐心等待...'
    }
    if ($pipExitCode -eq 0) {
        return
    }

    Write-Step '镜像源安装失败，正在切换到官方 PyPI...'
    $fallbackArguments = @('-u', '-m', 'pip', 'install', '--disable-pip-version-check') + $compatibleDisplayArguments + @('--index-url', $OfficialPipSource) + $Arguments
    $fallbackExitCode = Invoke-VisibleProcess -FilePath $VenvPython -Arguments $fallbackArguments -HeartbeatMessage '官方 PyPI 仍在以兼容模式处理依赖，请耐心等待...'
    if ($fallbackExitCode -ne 0) {
        throw "命令执行失败，退出代码 ${fallbackExitCode}：$VenvPython $($fallbackArguments -join ' ')"
    }
}

function Get-CommandPath {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) {
        return $null
    }
    foreach ($propertyName in @('Path', 'Source', 'Definition')) {
        $property = $command.PSObject.Properties[$propertyName]
        if ($property) {
            $value = [string]$property.Value
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return $value
            }
        }
    }
    return $null
}

function Test-PythonCandidate {
    param(
        [string]$FilePath,
        [string[]]$BaseArguments = @()
    )

    if (-not $FilePath) {
        return $null
    }
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $minimumParts = $MinimumPythonVersion.Split('.')
    $versionCheck = "import sys; raise SystemExit(0 if sys.version_info >= ($($minimumParts[0]), $($minimumParts[1])) else 1)"
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
        $candidate = Test-PythonCandidate -FilePath $pyLauncher -BaseArguments @('-3')
        if ($candidate) {
            return $candidate
        }
    }

    foreach ($name in @('python', 'python3', 'python3.14', 'python3.13', 'python3.12', 'python3.11')) {
        $path = Get-CommandPath $name
        $candidate = Test-PythonCandidate -FilePath $path
        if ($candidate) {
            return $candidate
        }
    }

    $pythonPaths = @()
    foreach ($root in @(
        (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\Python'),
        [Environment]::GetEnvironmentVariable('ProgramFiles'),
        [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
    )) {
        if ([string]::IsNullOrWhiteSpace($root) -or -not (Test-Path -LiteralPath $root)) { continue }
        $pythonPaths += Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like 'Python*' } |
            ForEach-Object { Join-Path $_.FullName 'python.exe' }
    }
    $pythonPaths += @(
        (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Microsoft\WindowsApps\python.exe'),
        (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\Python\Launcher\py.exe')
    )
    foreach ($path in @($pythonPaths | Select-Object -Unique)) {
        $candidate = Test-PythonCandidate -FilePath $path
        if ($candidate) { return $candidate }
    }

    foreach ($registryPath in @(
        'HKCU:\Software\Python\PythonCore',
        'HKLM:\Software\Python\PythonCore',
        'HKLM:\Software\WOW6432Node\Python\PythonCore'
    )) {
        foreach ($versionKey in @(Get-ChildItem -Path $registryPath -ErrorAction SilentlyContinue | Sort-Object PSChildName -Descending)) {
            $installPath = $null
            $installPathKey = Get-Item -LiteralPath (Join-Path $versionKey.PSPath 'InstallPath') -ErrorAction SilentlyContinue
            if ($installPathKey) {
                $installPath = [string]$installPathKey.GetValue('')
            }
            if ($installPath) {
                $candidate = Test-PythonCandidate -FilePath (Join-Path $installPath 'python.exe')
                if ($candidate) { return $candidate }
            }
        }
    }
    return $null
}

function Find-PreferredPythonWithRetry {
    param(
        [int]$Attempts = 12,
        [int]$DelayMilliseconds = 500
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        Refresh-ProcessPath
        $python = Find-PreferredPython
        if ($python) {
            return $python
        }
        if ($attempt -lt $Attempts) {
            Start-Sleep -Milliseconds $DelayMilliseconds
        }
    }
    return $null
}

function Get-LatestPythonInstaller {
    param([Parameter(Mandatory = $true)][string]$MirrorRoot)

    $listing = Invoke-WebRequest -UseBasicParsing -Uri "$MirrorRoot/" -TimeoutSec 60
    $entryGroups = @($listing.Content | ConvertFrom-Json)
    $candidates = @()
    foreach ($entryGroup in $entryGroups) {
        foreach ($entry in @($entryGroup)) {
            if ($null -eq $entry) { continue }
            $nameProperty = $entry.PSObject.Properties['name']
            if (-not $nameProperty) { continue }
            $name = [string]$nameProperty.Value
            if ($name -match '^3\.13\.(\d+)/$') {
                $candidates += [PSCustomObject]@{
                    Name = $name
                    Patch = [int]$Matches[1]
                }
            }
        }
    }
    $latestEntry = @($candidates | Sort-Object Patch -Descending | Select-Object -First 1)
    if ($latestEntry.Count -eq 0) {
        throw '镜像目录中没有找到可用的 Python 3.13.x 版本。'
    }

    $version = $latestEntry[0].Name.TrimEnd('/')
    $architecture = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITEW6432')
    if ([string]::IsNullOrWhiteSpace($architecture)) {
        $architecture = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE')
    }
    $installerName = switch ($architecture) {
        'ARM64' { "python-$version-arm64.exe" }
        'x86' { "python-$version.exe" }
        default { "python-$version-amd64.exe" }
    }
    return [PSCustomObject]@{
        Version = $version
        Name = $installerName
        Url = "$MirrorRoot/$version/$installerName"
    }
}

function Install-PythonFromMirror {
    $installer = Get-LatestPythonInstaller -MirrorRoot $PythonInstallMirror
    $installerPath = Join-Path ([IO.Path]::GetTempPath()) "elainabot-$($installer.Name)"

    Write-Step "winget 安装不可用，正在通过镜像下载 Python $($installer.Version)..."
    try {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri $installer.Url -OutFile $installerPath -TimeoutSec 300
        } catch {
            if (-not $UseRichConsoleOutput) {
                throw
            }
            Write-Step 'Python 下载未完成，正在关闭动态进度并以兼容模式重试...'
            Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
            $previousProgressPreference = $ProgressPreference
            try {
                $ProgressPreference = 'SilentlyContinue'
                Invoke-WebRequest -UseBasicParsing -Uri $installer.Url -OutFile $installerPath -TimeoutSec 300
            } finally {
                $ProgressPreference = $previousProgressPreference
            }
        }
        Write-Step 'Python 安装包下载完成，正在以当前用户权限静默安装...'
        $installerArguments = @(
            '/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_launcher=1',
            'InstallLauncherAllUsers=0', 'Include_pip=1', 'Include_test=0',
            'Include_doc=0', 'Include_debug=0', 'Include_symbols=0'
        )
        $installerProcess = Start-Process -FilePath $installerPath -ArgumentList $installerArguments -Wait -PassThru
        $installerExitCode = $installerProcess.ExitCode
        if ($installerExitCode -notin @(0, 3010)) {
            throw "Python 安装程序退出代码：$installerExitCode"
        }
    } finally {
        Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
    }

    $python = Find-PreferredPythonWithRetry
    if (-not $python) {
        throw 'Python 安装程序已结束，但没有找到可用的 Python 3.11+。'
    }
    return $python
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
    Write-Step "[1/6] 正在检查 Python $MinimumPythonVersion 或更高版本..."
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
    if (-not $python) {
        $wingetPath = Get-CommandPath 'winget'
        if ($wingetPath) {
            Write-Step '未找到 Python 3.11+，正在以当前用户权限安装 Python 3.13...'
            $wingetExitCode = 1
            $previousPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = 'Continue'
                $wingetArguments = @(
                    'install', '--id', 'Python.Python.3.13', '--exact', '--source', 'winget',
                    '--scope', 'user', '--accept-package-agreements', '--accept-source-agreements',
                    '--disable-interactivity', '--silent'
                )
                if ($UseRichConsoleOutput) {
                    & $wingetPath @wingetArguments
                    $wingetExitCode = $LASTEXITCODE
                    if ($wingetExitCode -ne 0) {
                        Write-Step "winget 动态输出未完成（退出代码 $wingetExitCode），正在以兼容模式重试..."
                        $wingetExitCode = Invoke-VisibleProcess -FilePath $wingetPath -Arguments $wingetArguments -HeartbeatMessage 'winget 仍在以兼容模式下载或安装 Python，请耐心等待...' -ForwardOutput $false
                    }
                } else {
                    $wingetExitCode = Invoke-VisibleProcess -FilePath $wingetPath -Arguments $wingetArguments -HeartbeatMessage 'winget 仍在下载或安装 Python，请耐心等待...' -ForwardOutput $false
                }
            } finally {
                $ErrorActionPreference = $previousPreference
            }
            if ($wingetExitCode -ne 0) {
                Write-Step "winget 安装失败（退出代码 $wingetExitCode），正在切换到 Python 镜像。"
            }
            $python = Find-PreferredPythonWithRetry
        }
    }
    if (-not $python) {
        $python = Install-PythonFromMirror
    }

    Write-Step "[1/6] 已找到兼容的 Python：$($python.Version)"
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
    throw '系统 Python 无法创建虚拟环境，请确认 Python 安装包含 venv 模块。'
}

function Test-FrameworkComplete {
    $required = @(
        'main.py',
        'requirements.txt',
        'pyproject.toml',
        'config/settings.example.yaml',
        'core/application.py',
        'core/base/config.py',
        'web/setup.py'
    )
    foreach ($relativePath in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $RootDir $relativePath) -PathType Leaf)) {
            return $false
        }
    }
    return $true
}

function Get-FrameworkDownloadUrls {
    $customMirror = [string]$env:ELAINABOT_FRAMEWORK_MIRROR
    if (-not [string]::IsNullOrWhiteSpace($customMirror)) {
        $customMirror = $customMirror.Trim().TrimEnd('/')
        if ($customMirror -match '\.(zip)(\?.*)?$') {
            Write-Output $customMirror
        } else {
            Write-Output ("$customMirror/$FrameworkArchiveUrl")
        }
    }
    foreach ($mirror in $FrameworkMirrors) {
        Write-Output ("$($mirror.TrimEnd('/'))/$FrameworkArchiveUrl")
    }
    Write-Output $FrameworkArchiveUrl
}

function Get-AvailableFrameworkDownloadUrl {
    $urls = @(Get-FrameworkDownloadUrls | Select-Object -Unique)
    if ($urls.Count -eq 0) {
        throw '没有可用的框架下载地址。'
    }

    Write-Step "正在并发检测 $($urls.Count) 个框架下载源..."
    $probeSource = @'
import concurrent.futures
import os
import socket
import sys
import urllib.request

urls = [line for line in os.environ['ELAINABOT_FRAMEWORK_PROBE_URLS'].splitlines() if line]
zip_signatures = (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')

default_getaddrinfo = socket.getaddrinfo
def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    results = default_getaddrinfo(host, port, family, type, proto, flags)
    ipv4_results = [item for item in results if item[0] == socket.AF_INET]
    return ipv4_results or results
socket.getaddrinfo = ipv4_getaddrinfo

proxy_config = urllib.request.getproxies()

def probe(item):
    index, url = item
    openers = [urllib.request.build_opener(urllib.request.ProxyHandler({}))]
    if any(name in proxy_config for name in ('http', 'https', 'all')):
        openers.append(urllib.request.build_opener(urllib.request.ProxyHandler(proxy_config)))
    for opener in openers:
        try:
            request = urllib.request.Request(
                url,
                headers={
                    'User-Agent': 'ElainaBot-Startup-Mirror-Test',
                    'Accept': 'application/zip, application/octet-stream;q=0.9, */*;q=0.1',
                    'Accept-Encoding': 'identity',
                    'Range': 'bytes=0-3',
                },
            )
            with opener.open(request, timeout=3) as response:
                if response.read(4) in zip_signatures:
                    return index
        except Exception:
            pass
    return None

with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(urls))) as executor:
    available = [index for index in executor.map(probe, enumerate(urls)) if index is not None]

if not available:
    raise SystemExit(1)
print(urls[min(available)])
'@

    $previousProbeUrls = $env:ELAINABOT_FRAMEWORK_PROBE_URLS
    try {
        $env:ELAINABOT_FRAMEWORK_PROBE_URLS = $urls -join "`n"
        $previousPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            $probeOutput = @($probeSource | & $VenvPython - 2>&1)
            $probeExitCode = $LASTEXITCODE
        } finally {
            $ErrorActionPreference = $previousPreference
        }
    } finally {
        if ($null -eq $previousProbeUrls) {
            Remove-Item Env:ELAINABOT_FRAMEWORK_PROBE_URLS -ErrorAction SilentlyContinue
        } else {
            $env:ELAINABOT_FRAMEWORK_PROBE_URLS = $previousProbeUrls
        }
    }

    if ($probeExitCode -ne 0 -or $probeOutput.Count -eq 0) {
        throw "下载框架失败，请手动下载：[https://github.com/ElainaCore/ElainaBot_v2]($FrameworkManualDownloadUrl)"
    }
    $availableUrl = $probeOutput[-1].ToString().Trim()
    if ([string]::IsNullOrWhiteSpace($availableUrl)) {
        throw "下载框架失败，请手动下载：[https://github.com/ElainaCore/ElainaBot_v2]($FrameworkManualDownloadUrl)"
    }
    Write-Step "已找到可用框架下载源: $availableUrl"
    return $availableUrl
}
function Invoke-FrameworkArchiveDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$DestinationPath
    )

    $downloadSource = @'
import os
import shutil
import socket
import sys
import urllib.request
from pathlib import Path

url = os.environ['ELAINABOT_DOWNLOAD_URL']
destination = Path(os.environ['ELAINABOT_DOWNLOAD_DESTINATION'])
partial = destination.with_name(destination.name + '.part')
show_progress = os.environ.get('ELAINABOT_SHOW_PROGRESS') == '1'

default_getaddrinfo = socket.getaddrinfo
def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    results = default_getaddrinfo(host, port, family, type, proto, flags)
    ipv4_results = [item for item in results if item[0] == socket.AF_INET]
    return ipv4_results or results
socket.getaddrinfo = ipv4_getaddrinfo

proxy_config = urllib.request.getproxies()
openers = [('direct-ipv4', urllib.request.build_opener(urllib.request.ProxyHandler({})))]
if any(name in proxy_config for name in ('http', 'https', 'all')):
    openers.append(('system-proxy-ipv4', urllib.request.build_opener(urllib.request.ProxyHandler(proxy_config))))

errors = []
for mode, opener in openers:
    try:
        request = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'ElainaBot-Startup-Downloader',
                'Accept': 'application/zip, application/octet-stream;q=0.9, */*;q=0.1',
                'Accept-Encoding': 'identity',
            },
        )
        with opener.open(request, timeout=30) as response, partial.open('wb') as output:
            if not show_progress:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            else:
                total = int(response.headers.get('Content-Length') or 0)
                downloaded = 0
                last_percent = -1
                try:
                    progress_output = open('CONOUT$', 'w', encoding='ascii', errors='replace', buffering=1)
                    close_progress_output = True
                except OSError:
                    progress_output = sys.stderr
                    close_progress_output = False
                try:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            percent = min(100, downloaded * 100 // total)
                            if percent != last_percent:
                                filled = percent * 30 // 100
                                bar = '#' * filled + '-' * (30 - filled)
                                progress_output.write(
                                    f'\r[ElainaBot] Download [{bar}] {percent:3d}% '
                                    f'{downloaded / 1048576:.1f}/{total / 1048576:.1f} MiB'
                                )
                                last_percent = percent
                        else:
                            progress_output.write(
                                f'\r[ElainaBot] Downloaded {downloaded / 1048576:.1f} MiB'
                            )
                    progress_output.write('\n')
                finally:
                    if close_progress_output:
                        progress_output.close()
        os.replace(partial, destination)
        print(mode)
        raise SystemExit(0)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        errors.append(f'{mode}: {type(exc).__name__}: {exc}')

print(' | '.join(errors), file=sys.stderr)
raise SystemExit(1)
'@
    $previousUrl = $env:ELAINABOT_DOWNLOAD_URL
    $previousDestination = $env:ELAINABOT_DOWNLOAD_DESTINATION
    $previousShowProgress = $env:ELAINABOT_SHOW_PROGRESS
    try {
        $env:ELAINABOT_DOWNLOAD_URL = $Url
        $env:ELAINABOT_DOWNLOAD_DESTINATION = $DestinationPath
        $progressModes = @('0')
        if ($UseRichConsoleOutput) {
            $progressModes = @('1', '0')
        }
        $downloadOutput = @()
        $downloadExitCode = 1
        foreach ($progressMode in $progressModes) {
            $env:ELAINABOT_SHOW_PROGRESS = $progressMode
            $previousPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = 'Continue'
                $downloadOutput = @($downloadSource | & $VenvPython - 2>&1)
                $downloadExitCode = $LASTEXITCODE
            } finally {
                $ErrorActionPreference = $previousPreference
            }
            if ($downloadExitCode -eq 0) {
                break
            }
            if ($progressMode -eq '1') {
                Write-Step '框架下载的动态进度未完成，正在关闭动态进度并以兼容模式重试...'
            }
        }
    } finally {
        if ($null -eq $previousUrl) { Remove-Item Env:ELAINABOT_DOWNLOAD_URL -ErrorAction SilentlyContinue } else { $env:ELAINABOT_DOWNLOAD_URL = $previousUrl }
        if ($null -eq $previousDestination) { Remove-Item Env:ELAINABOT_DOWNLOAD_DESTINATION -ErrorAction SilentlyContinue } else { $env:ELAINABOT_DOWNLOAD_DESTINATION = $previousDestination }
        if ($null -eq $previousShowProgress) { Remove-Item Env:ELAINABOT_SHOW_PROGRESS -ErrorAction SilentlyContinue } else { $env:ELAINABOT_SHOW_PROGRESS = $previousShowProgress }
    }
    if ($downloadExitCode -ne 0) {
        $details = ($downloadOutput | Select-Object -Last 5 | ForEach-Object { $_.ToString() }) -join ' '
        throw "下载失败：$details"
    }
    $downloadMode = ($downloadOutput | Select-Object -Last 1).ToString()
    Write-Step "框架压缩包下载完成（$downloadMode）。"
}

function Restore-FrameworkArchive {
    param(
        [Parameter(Mandatory = $true)][string]$ArchivePath,
        [Parameter(Mandatory = $true)][string]$StagingPath
    )

    $restoreSource = @'
import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path

archive = Path(os.environ['ELAINABOT_RESTORE_ARCHIVE'])
staging = Path(os.environ['ELAINABOT_RESTORE_STAGING'])
root = Path(os.environ['ELAINABOT_RESTORE_ROOT'])
staging = staging.resolve()
root = root.resolve()
with zipfile.ZipFile(archive) as zf:
    for info in zf.infolist():
        name = info.filename.replace('\\', '/')
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError(f'压缩包包含不安全路径: {name}')
        mode = (info.external_attr >> 16) & 0o170000
        if mode == stat.S_IFLNK:
            raise RuntimeError(f'压缩包包含不安全符号链接: {name}')
        target = (staging / relative).resolve()
        if target != staging and staging not in target.parents:
            raise RuntimeError(f'压缩包包含不安全路径: {name}')
        if info.is_dir() or name.endswith('/'):
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as source, target.open('wb') as destination:
            shutil.copyfileobj(source, destination)

entries = list(staging.iterdir())
source = entries[0] if len(entries) == 1 and entries[0].is_dir() else staging
required = (
    'main.py',
    'requirements.txt',
    'pyproject.toml',
    'config/settings.example.yaml',
    'core/application.py',
    'core/base/config.py',
    'web/setup.py',
)
missing = [relative for relative in required if not (source / relative).is_file()]
if missing:
    raise RuntimeError('压缩包缺少框架基本文件: ' + ', '.join(missing))

for item in source.rglob('*'):
    relative = item.relative_to(source)
    destination = root / relative
    resolved_destination = destination.resolve()
    if resolved_destination != root and root not in resolved_destination.parents:
        raise RuntimeError(f'目标路径超出项目目录: {relative}')
    if item.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
    elif item.is_file() and not os.path.lexists(destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, destination)
'@
    $previousArchive = $env:ELAINABOT_RESTORE_ARCHIVE
    $previousStaging = $env:ELAINABOT_RESTORE_STAGING
    $previousRoot = $env:ELAINABOT_RESTORE_ROOT
    try {
        $env:ELAINABOT_RESTORE_ARCHIVE = $ArchivePath
        $env:ELAINABOT_RESTORE_STAGING = $StagingPath
        $env:ELAINABOT_RESTORE_ROOT = $RootDir
        $restoreSource | & $VenvPython -
        $restoreExitCode = $LASTEXITCODE
    } finally {
        if ($null -eq $previousArchive) { Remove-Item Env:ELAINABOT_RESTORE_ARCHIVE -ErrorAction SilentlyContinue } else { $env:ELAINABOT_RESTORE_ARCHIVE = $previousArchive }
        if ($null -eq $previousStaging) { Remove-Item Env:ELAINABOT_RESTORE_STAGING -ErrorAction SilentlyContinue } else { $env:ELAINABOT_RESTORE_STAGING = $previousStaging }
        if ($null -eq $previousRoot) { Remove-Item Env:ELAINABOT_RESTORE_ROOT -ErrorAction SilentlyContinue } else { $env:ELAINABOT_RESTORE_ROOT = $previousRoot }
    }
    if ($restoreExitCode -ne 0) {
        return $false
    }
    return $true
}

function Ensure-Framework {
    $required = @(
        'main.py',
        'requirements.txt',
        'pyproject.toml',
        'config/settings.example.yaml',
        'core/application.py',
        'core/base/config.py',
        'web/setup.py'
    )
    $missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $RootDir $_) -PathType Leaf) })
    if ($missing.Count -eq 0) {
        Write-Step '[3/6] 框架基本文件完整，无需下载。'
        return
    }

    Write-Step ("[3/6] 缺少框架基本文件: $($missing -join ', ')，正在检测镜像...")
    $downloadUrl = Get-AvailableFrameworkDownloadUrl
    $staging = Join-Path ([IO.Path]::GetTempPath()) ("elainabot-framework-$([guid]::NewGuid().ToString('N'))")
    $extractPath = Join-Path $staging 'extracted'
    $archivePath = Join-Path $staging 'framework.zip'
    New-Item -ItemType Directory -Path $extractPath -Force | Out-Null
    try {
        foreach ($url in @($downloadUrl)) {
            Write-Step "正在下载框架镜像: $url"
            Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
            try {
                Invoke-FrameworkArchiveDownload -Url $url -DestinationPath $archivePath
                & $VenvPython -c "import zipfile,sys; raise SystemExit(0 if zipfile.is_zipfile(sys.argv[1]) else 1)" $archivePath
                if ($LASTEXITCODE -ne 0) {
                    throw '下载内容不是有效 ZIP'
                }
                Remove-Item -LiteralPath $extractPath -Recurse -Force -ErrorAction SilentlyContinue
                New-Item -ItemType Directory -Path $extractPath -Force | Out-Null
                if ((Restore-FrameworkArchive -ArchivePath $archivePath -StagingPath $extractPath) -and (Test-FrameworkComplete)) {
                    Write-Step '框架基本文件已从镜像恢复。'
                    return
                }
                Write-Step '镜像压缩包解压后仍缺少框架文件，尝试下一个来源。'
            } catch {
                Write-Step "镜像下载或解压失败，尝试下一个来源：$($_.Exception.Message)"
            }
        }
        throw "下载框架失败，请手动下载：[https://github.com/ElainaCore/ElainaBot_v2]($FrameworkManualDownloadUrl)"
    } finally {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
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
    Write-Step '[4/6] 正在扫描框架、模块和插件的依赖文件...'
    $requirements = @(Get-RequirementFiles)
    if ($requirements.Count -eq 0) {
        throw '未找到任何依赖文件。'
    }
    Write-Step "[4/6] 已找到 $($requirements.Count) 个依赖文件。"

    $fingerprint = Get-RequirementsFingerprint $requirements
    $savedFingerprint = if (Test-Path -LiteralPath $StampFile) {
        (Get-Content -LiteralPath $StampFile -Raw).Trim()
    } else {
        ''
    }

    if ($savedFingerprint -eq $fingerprint -and (Test-CoreDependencies)) {
        Write-DependencyProgress -Percent 100 -Activity '依赖已是最新状态'
        Write-Step '[5/6] 框架依赖已经安装且为最新状态，无需重复安装。'
        return
    }

    Write-Step "[5/6] 正在根据 $($requirements.Count) 个依赖文件安装框架依赖..."
    Write-DependencyProgress -Percent 5 -Activity '正在准备 pip'
    Write-Step '[5/6] 正在准备 pip 安装工具...'
    & $VenvPython -m ensurepip --upgrade 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "pip 安装工具准备失败，退出代码 ${LASTEXITCODE}。"
    }
    Write-DependencyProgress -Percent 15 -Activity '正在更新基础安装工具'
    Invoke-PipInstall -Arguments @('--upgrade', 'pip', 'setuptools', 'wheel')
    Write-DependencyProgress -Percent 30 -Activity '基础安装工具已就绪'

    $arguments = @()
    foreach ($requirement in $requirements) {
        $arguments += @('-r', $requirement.FullName)
    }
    Write-DependencyProgress -Percent 35 -Activity "正在安装 $($requirements.Count) 个依赖清单"
    Invoke-PipInstall -Arguments $arguments
    Write-DependencyProgress -Percent 90 -Activity '依赖安装完成，正在验证核心包'

    if (-not (Test-CoreDependencies)) {
        throw '依赖安装已经结束，但仍有一个或多个核心包无法导入。'
    }
    Set-Content -LiteralPath $StampFile -Value $fingerprint -Encoding ASCII
    Write-DependencyProgress -Percent 100 -Activity '框架依赖安装完成'
    Write-Step '[5/6] 框架依赖安装完成并通过验证。'
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
    if ($UseSystemBrowserPanel) {
        Write-Step '[5/6] 当前 Windows 不支持内嵌桌面窗口，将使用外部浏览器打开管理面板。'
        return
    }

    Write-Step '[5/6] 正在检查 Windows 桌面窗口组件...'
    if (Test-WebPanelDependency) {
        Write-Step '[5/6] Windows 桌面窗口组件已经安装，无需重复安装。'
        return
    }

    Write-Step '[5/6] 正在安装启动脚本专用的 Windows 桌面窗口组件...'
    Write-DependencyProgress -Percent 92 -Activity '正在安装桌面窗口组件'
    Write-Step '[5/6] 正在准备 pip 安装工具...'
    & $VenvPython -m ensurepip --upgrade 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "pip 安装工具准备失败，退出代码 ${LASTEXITCODE}。"
    }
    Invoke-PipInstall -Arguments @($WebPanelPackage)
    if (-not (Test-WebPanelDependency)) {
        throw 'Windows 桌面窗口组件安装结束，但 pywebview 仍无法导入。'
    }
    Write-DependencyProgress -Percent 100 -Activity '桌面窗口组件安装完成'
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

    $windowSource = if ($UseSystemBrowserPanel) {
        @'
import os
import shutil
import subprocess
import sys
import time
import urllib.request

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

browser_candidates = (
    'msedge.exe',
    'chrome.exe',
    'firefox.exe',
    'iexplore.exe',
)
browser_paths = []
for name in browser_candidates:
    resolved = shutil.which(name)
    if resolved:
        browser_paths.append(resolved)

for base_name in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
    base_path = os.environ.get(base_name)
    if not base_path:
        continue
    browser_paths.extend((
        os.path.join(base_path, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        os.path.join(base_path, 'Google', 'Chrome', 'Application', 'chrome.exe'),
        os.path.join(base_path, 'Mozilla Firefox', 'firefox.exe'),
    ))

for browser_path in browser_paths:
    if os.path.isfile(browser_path):
        subprocess.Popen([browser_path, panel_url])
        raise SystemExit(0)

raise SystemExit(2)
'@
    } else {
        @'
import sys
import time
import urllib.request
import webbrowser

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

window = webview.create_window(
    'ElainaBot 管理面板',
    panel_url,
    width=1280,
    height=820,
    min_size=(960, 640),
)

_native_toolbar_refs = []


def add_native_refresh_toolbar():
    import clr

    clr.AddReference('System.Windows.Forms')
    clr.AddReference('System.Drawing')

    import System.Windows.Forms as WinForms
    from System import Action
    from System.Drawing import Color, Font

    form = window.native

    def install_toolbar():
        browser = getattr(form, 'browser', None)
        browser_control = getattr(browser, 'webview', None) if browser is not None else None
        if browser_control is None:
            return

        layout = WinForms.TableLayoutPanel()
        layout.Name = 'ElainaBotWindowLayout'
        layout.Dock = WinForms.DockStyle.Fill
        layout.Margin = WinForms.Padding(0)
        layout.Padding = WinForms.Padding(0)
        layout.ColumnCount = 1
        layout.RowCount = 2
        layout.ColumnStyles.Add(WinForms.ColumnStyle(WinForms.SizeType.Percent, 100.0))
        layout.RowStyles.Add(WinForms.RowStyle(WinForms.SizeType.Absolute, 38.0))
        layout.RowStyles.Add(WinForms.RowStyle(WinForms.SizeType.Percent, 100.0))

        toolbar = WinForms.ToolStrip()
        toolbar.Name = 'ElainaBotWindowToolbar'
        toolbar.Dock = WinForms.DockStyle.Fill
        toolbar.AutoSize = False
        toolbar.Height = 38
        toolbar.Margin = WinForms.Padding(0)
        toolbar.GripStyle = WinForms.ToolStripGripStyle.Hidden
        toolbar.RenderMode = WinForms.ToolStripRenderMode.System
        toolbar.Padding = WinForms.Padding(8, 4, 8, 4)
        toolbar.BackColor = Color.FromArgb(248, 249, 250)

        refresh_button = WinForms.ToolStripButton()
        refresh_button.Name = 'ElainaBotRefreshButton'
        refresh_button.Text = '刷新'
        refresh_button.ToolTipText = '刷新管理面板'
        refresh_button.AccessibleName = '刷新管理面板'
        refresh_button.DisplayStyle = WinForms.ToolStripItemDisplayStyle.Text
        refresh_button.Font = Font('Microsoft YaHei UI', 9.0)
        refresh_button.AutoSize = True
        refresh_button.Padding = WinForms.Padding(6, 0, 6, 0)

        copy_link_button = WinForms.ToolStripButton()
        copy_link_button.Name = 'ElainaBotCopyLinkButton'
        copy_link_button.Text = '复制链接'
        copy_link_button.ToolTipText = '复制管理面板链接到剪贴板'
        copy_link_button.AccessibleName = '复制管理面板链接'
        copy_link_button.DisplayStyle = WinForms.ToolStripItemDisplayStyle.Text
        copy_link_button.Font = Font('Microsoft YaHei UI', 9.0)
        copy_link_button.AutoSize = True
        copy_link_button.Padding = WinForms.Padding(6, 0, 6, 0)

        open_browser_button = WinForms.ToolStripButton()
        open_browser_button.Name = 'ElainaBotOpenBrowserButton'
        open_browser_button.Text = '前往浏览器打开'
        open_browser_button.ToolTipText = '使用默认浏览器打开管理面板'
        open_browser_button.AccessibleName = '使用默认浏览器打开管理面板'
        open_browser_button.DisplayStyle = WinForms.ToolStripItemDisplayStyle.Text
        open_browser_button.Font = Font('Microsoft YaHei UI', 9.0)
        open_browser_button.AutoSize = True
        open_browser_button.Padding = WinForms.Padding(6, 0, 6, 0)

        def refresh_panel(*_):
            try:
                browser = getattr(form, 'browser', None)
                native_webview = getattr(browser, 'webview', None) if browser is not None else None
                try:
                    core_webview = getattr(native_webview, 'CoreWebView2', None)
                except Exception:
                    core_webview = None
                if core_webview is not None:
                    core_webview.Reload()
                elif native_webview is not None and hasattr(native_webview, 'Refresh'):
                    native_webview.Refresh()
                else:
                    window.load_url(panel_url)
            except Exception:
                # The browser may still be initializing; retry through the
                # public pywebview API instead of breaking the native window.
                try:
                    window.load_url(panel_url)
                except Exception:
                    pass

        def copy_panel_link(*_):
            try:
                WinForms.Clipboard.SetText(panel_url)
                copy_link_button.Text = '已复制'
            except Exception:
                copy_link_button.Text = '复制失败'

            reset_timer = WinForms.Timer()
            reset_timer.Interval = 1500

            def reset_copy_button(*_):
                reset_timer.Stop()
                reset_timer.Dispose()
                copy_link_button.Text = '复制链接'
                try:
                    _native_toolbar_refs.remove((reset_timer, reset_copy_button))
                except ValueError:
                    pass

            reset_timer.Tick += reset_copy_button
            _native_toolbar_refs.append((reset_timer, reset_copy_button))
            reset_timer.Start()

        def open_panel_in_browser(*_):
            try:
                webbrowser.open(panel_url, new=2)
            except Exception:
                pass

        refresh_button.Click += refresh_panel
        copy_link_button.Click += copy_panel_link
        open_browser_button.Click += open_panel_in_browser
        toolbar.Items.Add(refresh_button)
        toolbar.Items.Add(WinForms.ToolStripSeparator())
        toolbar.Items.Add(copy_link_button)
        toolbar.Items.Add(open_browser_button)

        form.SuspendLayout()
        try:
            if browser_control.Parent is not None:
                browser_control.Parent.Controls.Remove(browser_control)
            browser_control.Dock = WinForms.DockStyle.Fill
            browser_control.Margin = WinForms.Padding(0)
            layout.Controls.Add(toolbar, 0, 0)
            layout.Controls.Add(browser_control, 0, 1)
            form.Controls.Add(layout)
        finally:
            form.ResumeLayout(True)

        # Keep the managed controls and Python delegate alive for the window lifetime.
        _native_toolbar_refs.append((
            layout, toolbar, refresh_button, copy_link_button, open_browser_button,
            refresh_panel, copy_panel_link, open_panel_in_browser,
        ))

    form.BeginInvoke(Action(install_toolbar))


window.events.shown += add_native_refresh_toolbar
webview.start()
'@
    }

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
    if (-not $UseLegacyWindowsPath -and -not $UseRichConsoleOutput) {
        Write-Step '当前控制台无法可靠显示动态进度，已自动切换到纯文本兼容模式。'
    }
    Ensure-VirtualEnvironment
    Ensure-Framework
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
        Write-Step "[6/6] 检测到框架已经运行，仅重新打开管理面板。"
        $existingPanelWindow = Start-WebPanelWindow -Url $panelUrl
        if ($existingPanelWindow) {
            $existingPanelWindow.Dispose()
        }
        Write-Step '管理面板已打开，无需重新启动框架。'
        exit 0
    }

    Write-Step "[6/6] 配置端口 $panelPort 尚未开启，正在启动 ElainaBot 框架..."
    Write-Step '面板就绪后将自动打开 ElainaBot 管理面板。'
    # Keep the framework in the foreground on every supported Windows version
    # so this console remains available for runtime logs and diagnostics.
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
