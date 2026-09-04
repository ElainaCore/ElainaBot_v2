#!/usr/bin/env bash
# 被 sh/dash/busybox 以 `sh start.sh` 调用时切换到 bash 重新执行。
if [ -z "${BASH_VERSION:-}" ]; then
    if ! command -v bash >/dev/null 2>&1; then
        # Alpine 等缺 bash 的环境尝试自动安装后重新执行。
        if command -v apk >/dev/null 2>&1; then
            echo '[ElainaBot] 当前系统缺少 bash，正在自动安装...'
            if [ "$(id -u)" = 0 ]; then
                apk add --no-cache bash && exec bash "$0" "$@"
            elif command -v sudo >/dev/null 2>&1; then
                sudo apk add --no-cache bash && exec bash "$0" "$@"
            fi
        fi
        printf '[ElainaBot] 错误：本脚本需要 bash，请先安装 bash（例如 apt install bash 或 apk add bash）后用 bash start.sh 运行。\n' >&2
        exit 1
    fi
    exec bash "$0" "$@"
fi
set -Eeuo pipefail

BOOTSTRAP_VERSION='5'
PYTHON_VERSION='3.13'
DEFAULT_PYTHON_INSTALL_MIRROR='https://registry.npmmirror.com/-/binary/python-build-standalone'
PYTHON_INSTALL_MIRROR="${ELAINABOT_PYTHON_MIRROR:-$DEFAULT_PYTHON_INSTALL_MIRROR}"
PYTHON_INSTALL_MIRROR="${PYTHON_INSTALL_MIRROR%/}"
PIP_MIRROR="${ELAINABOT_PIP_MIRROR:-https://pypi.tuna.tsinghua.edu.cn/simple}"
OFFICIAL_PIP_SOURCE="${ELAINABOT_OFFICIAL_PIP_SOURCE:-https://pypi.org/simple}"
FRAMEWORK_DOWNLOAD_URL="https://github.com/ElainaCore/ElainaBot_v2/archive/main.zip"
FRAMEWORK_MIRRORS=(
    "https://github.chenc.dev"
    "https://ghproxy.cfd"
    "https://github.tbedu.top"
    "https://ghproxy.cc"
)
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
TOOLS_DIR="$ROOT_DIR/.bootstrap/uv"
# uv 缓存固定在项目内，避免 ~/.cache 不可写（容器/受限主机）导致崩溃。
UV_CACHE_DIR="${ELAINABOT_UV_CACHE_DIR:-$ROOT_DIR/.bootstrap/uv-cache}"
UV_INSTALLER_URL="https://astral.sh/uv/install.sh"
UV_INSTALLER_FALLBACK_URL="https://github.com/astral-sh/uv/releases/latest/download/uv-installer.sh"
export UV_CACHE_DIR
STAMP_FILE="$VENV_DIR/.elainabot-requirements.sha256"
SETUP_ONLY=0

for argument in "$@"; do
    case "$argument" in
        --setup-only) SETUP_ONLY=1 ;;
        -h|--help)
            echo '用法：./start.sh [--setup-only]'
            exit 0
            ;;
        *)
            echo "[ElainaBot] 错误：未知参数：$argument" >&2
            exit 2
            ;;
    esac
done

cd "$ROOT_DIR"

step() {
    printf '[ElainaBot] %s\n' "$*"
}

fail() {
    printf '[ElainaBot] 错误：%s\n' "$*" >&2
    exit 1
}

run_as_root() {
    if (( EUID == 0 )); then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        fail "安装系统软件包需要 root 权限或 sudo：$*"
    fi
}

install_download_prerequisites() {
    step '未找到 curl 或 wget，正在安装下载工具...'
    if command -v apt-get >/dev/null 2>&1; then
        run_as_root apt-get update
        run_as_root apt-get install -y curl ca-certificates
    elif command -v microdnf >/dev/null 2>&1; then
        run_as_root microdnf install -y curl ca-certificates
    elif command -v dnf >/dev/null 2>&1; then
        run_as_root dnf install -y curl ca-certificates
    elif command -v yum >/dev/null 2>&1; then
        run_as_root yum install -y curl ca-certificates
    elif command -v pacman >/dev/null 2>&1; then
        run_as_root pacman -Sy --needed --noconfirm curl ca-certificates
    elif command -v zypper >/dev/null 2>&1; then
        run_as_root zypper --non-interactive install curl ca-certificates
    elif command -v apk >/dev/null 2>&1; then
        run_as_root apk add curl ca-certificates
    else
        fail '未找到受支持的软件包管理器。请安装 curl 或 wget 后重新运行本脚本。'
    fi
}

python_is_compatible() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1
}

# armv7/riscv64 等架构没有官方预编译 Python，需要提前给出明确指引。
ensure_downloadable_arch() {
    local arch
    arch="$(uname -m)"
    case "$arch" in
        x86_64|aarch64|riscv64|ppc64le|s390x) return 0 ;;
        armv7l|armv8l)
            # 预编译 Python 的 armv7 只有 glibc 构建，musl（Alpine）没有。
            if command -v apk >/dev/null 2>&1; then
                fail "未找到 Python 3.11+，且 musl 系统（Alpine）的 armv7 架构没有可自动下载的预编译 Python。请先执行 apk add python3 安装 Python 3.11+，再重新运行本脚本。"
            fi
            return 0
            ;;
        *)
            fail "未找到 Python 3.11+，且当前 CPU 架构（$arch）没有可自动下载的预编译 Python（支持 x86_64 / aarch64 / armv7 / riscv64 / ppc64le / s390x）。请先安装 Python 3.11 或更高版本（例如：apt install python3），再重新运行本脚本。"
            ;;
    esac
}

find_preferred_python() {
    local candidate
    for candidate in python3.13 python3.12 python3.11 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && python_is_compatible "$(command -v "$candidate")"; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

install_uv_installer() {
    local destination="$1" url mirror
    local -a sources=("$UV_INSTALLER_URL")
    for mirror in "${FRAMEWORK_MIRRORS[@]}"; do
        sources+=("${mirror%/}/$UV_INSTALLER_FALLBACK_URL")
    done
    sources+=("$UV_INSTALLER_FALLBACK_URL")
    for url in "${sources[@]}"; do
        if command -v curl >/dev/null 2>&1; then
            curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error --retry 1 --connect-timeout 10 --max-time 120 "$url" --output "$destination" && return 0
        else
            wget --quiet --timeout=15 --tries=1 --output-document="$destination" "$url" && return 0
        fi
    done
    return 1
}

ensure_downloader() {
    if command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1; then
        return
    fi
    install_download_prerequisites
}

uv_supports_python_mirror() {
    local uv_bin="$1"
    local help_output=''
    help_output="$("$uv_bin" python install --help 2>&1)" || return 1
    [[ "$help_output" == *'--mirror'* ]]
}

ensure_uv() {
    local system_uv=''
    local local_uv="$TOOLS_DIR/uv"

    if system_uv="$(command -v uv 2>/dev/null)" && uv_supports_python_mirror "$system_uv"; then
        printf '%s\n' "$system_uv"
        return
    fi
    if [[ -x "$local_uv" ]] && uv_supports_python_mirror "$local_uv"; then
        printf '%s\n' "$local_uv"
        return
    fi

    ensure_downloader >&2
    if [[ -n "$system_uv" || -e "$local_uv" ]]; then
        step '现有 Python 环境引导工具版本过旧，正在更新项目专用版本...' >&2
    else
        step '正在安装项目专用的 Python 环境引导工具...' >&2
    fi
    mkdir -p "$TOOLS_DIR" "$UV_CACHE_DIR"
    local installer
    installer="$(mktemp "${TMPDIR:-/tmp}/elainabot-uv-install.XXXXXX")"
    trap 'rm -f "$installer"' RETURN
    install_uv_installer "$installer" || fail '无法下载 Python 环境引导工具安装脚本，请检查网络后重试。'
    UV_INSTALL_DIR="$TOOLS_DIR" UV_NO_MODIFY_PATH=1 sh "$installer" >&2
    rm -f "$installer"
    trap - RETURN
    [[ -x "$local_uv" ]] || fail '无法安装项目专用的 Python 环境引导工具。'
    uv_supports_python_mirror "$local_uv" || fail '项目专用的 Python 环境引导工具不支持镜像下载，请稍后重试。'
    printf '%s\n' "$local_uv"
}

install_managed_python() {
    local uv_bin="$1"

    step "[1/6] 正在通过镜像下载项目专用的 Python $PYTHON_VERSION（将显示实时进度）..."
    if (
        unset UV_NO_PROGRESS
        "$uv_bin" --color always python install --no-bin \
            --mirror "$PYTHON_INSTALL_MIRROR" "$PYTHON_VERSION"
    ); then
        return
    fi

    step 'Python 镜像下载失败，正在切换到官方源...'
    (
        unset UV_NO_PROGRESS
        "$uv_bin" --color always python install --no-bin "$PYTHON_VERSION"
    ) || fail "Python $PYTHON_VERSION 下载失败，镜像源和官方源均不可用。"
}

backup_invalid_venv() {
    [[ -e "$VENV_DIR" || -L "$VENV_DIR" ]] || return
    local backup="$ROOT_DIR/.venv.backup-$(date +%Y%m%d-%H%M%S)"
    step "现有虚拟环境无效，正在将其移动到 ${backup##*/}。"
    mv -- "$VENV_DIR" "$backup"
}

ensure_virtual_environment() {
    step '[1/6] 正在检查 Python 3.11 或更高版本...'
    if [[ -x "$VENV_PYTHON" ]] && python_is_compatible "$VENV_PYTHON"; then
        step "[1/6] Python 已就绪：$("$VENV_PYTHON" -c 'import platform; print(platform.python_version())')"
        step '[2/6] 已有虚拟环境可用：.venv'
        return
    fi

    backup_invalid_venv
    local python_bin=''
    if python_bin="$(find_preferred_python)"; then
        step "[1/6] 已找到兼容的 Python：$("$python_bin" -c 'import platform; print(platform.python_version())')"
        step '[2/6] 正在创建项目虚拟环境：.venv...'
        if "$python_bin" -m venv "$VENV_DIR" && [[ -x "$VENV_PYTHON" ]]; then
            step '[2/6] 虚拟环境创建成功。'
            return
        fi
        if [[ -e "$VENV_DIR" ]]; then
            mv -- "$VENV_DIR" "$ROOT_DIR/.venv.failed-$(date +%Y%m%d-%H%M%S)"
        fi
        step '系统 Python 无法创建虚拟环境，将改用项目专用的 Python。'
    fi

    ensure_downloadable_arch
    local uv_bin
    uv_bin="$(ensure_uv)"
    install_managed_python "$uv_bin"
    step '[2/6] 正在创建项目虚拟环境：.venv...'
    "$uv_bin" --color always venv --python "$PYTHON_VERSION" \
        --managed-python --no-python-downloads "$VENV_DIR"
    [[ -x "$VENV_PYTHON" ]] || fail '虚拟环境创建结束，但未找到可用的 Python。'
    step '[2/6] 虚拟环境创建成功。'
}

framework_is_complete() {
    local required path
    required=(
        main.py
        requirements.txt
        pyproject.toml
        config/settings.example.yaml
        core/application.py
        core/base/config.py
        web/setup.py
    )
    for path in "${required[@]}"; do
        if [[ ! -f "$ROOT_DIR/$path" ]]; then
            return 1
        fi
    done
    return 0
}

framework_download_urls() {
    local custom_mirror="${ELAINABOT_FRAMEWORK_MIRROR:-}" mirror
    if [[ -n "$custom_mirror" ]]; then
        printf '%s\n' "${custom_mirror%/}/$FRAMEWORK_DOWNLOAD_URL"
    fi
    for mirror in "${FRAMEWORK_MIRRORS[@]}"; do
        printf '%s\n' "${mirror%/}/$FRAMEWORK_DOWNLOAD_URL"
    done
    printf '%s\n' "$FRAMEWORK_DOWNLOAD_URL"
}

restore_framework_from_archive() {
    local archive="$1" staging="$2"
    "$VENV_PYTHON" - "$archive" "$staging" "$ROOT_DIR" <<'PY'
import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path

archive, staging, root = map(Path, sys.argv[1:])
staging = staging.resolve()
root = root.resolve()
root.mkdir(parents=True, exist_ok=True)
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
PY
}

ensure_framework() {
    local path url archive staging
    local -a required download
    required=()
    for path in \
        main.py \
        requirements.txt \
        pyproject.toml \
        config/settings.example.yaml \
        core/application.py \
        core/base/config.py \
        web/setup.py; do
        [[ -f "$ROOT_DIR/$path" ]] || required+=("$path")
    done
    if (( ${#required[@]} == 0 )); then
        step '[3/6] 框架基本文件完整，无需下载。'
        return
    fi

    step "[3/6] 缺少框架基本文件: ${required[*]}，正在通过镜像下载并解压..."
    ensure_downloader
    mkdir -p "$TOOLS_DIR"
    staging="$(mktemp -d "${TMPDIR:-/tmp}/elainabot-framework.XXXXXX")"
    archive="$staging/framework.zip"
    while IFS= read -r url; do
        step "正在尝试框架镜像: $url"
        rm -f -- "$archive"
        if command -v curl >/dev/null 2>&1; then
            download=(curl --fail --location --silent --show-error --retry 2 --connect-timeout 10 --max-time 180 "$url" --output "$archive")
        else
            download=(wget --quiet --timeout=20 --tries=2 --output-document="$archive" "$url")
        fi
        if "${download[@]}" && "$VENV_PYTHON" -c 'import zipfile,sys; raise SystemExit(0 if zipfile.is_zipfile(sys.argv[1]) else 1)' "$archive"; then
            rm -rf -- "$staging/extracted"
            mkdir -p "$staging/extracted"
            if restore_framework_from_archive "$archive" "$staging/extracted"; then
                if framework_is_complete; then
                    step '框架基本文件已从镜像恢复。'
                    rm -rf -- "$staging"
                    return
                fi
                step '镜像压缩包解压后仍缺少框架文件，尝试下一个来源。'
            fi
        else
            step '镜像下载失败或返回的文件不是有效 ZIP，尝试下一个来源。'
        fi
    done < <(framework_download_urls)
    rm -rf -- "$staging"
    fail '框架基本文件缺失，镜像源和官方源均无法下载或解压。'
}

collect_requirement_files() {
    REQ_FILES=()
    local file directory
    while IFS= read -r -d '' file; do
        REQ_FILES+=("$file")
    done < <(find "$ROOT_DIR" -maxdepth 1 -type f \( -name 'requirements.txt' -o -name '*_requirements.txt' \) -print0 | sort -z)

    for directory in "$ROOT_DIR/modules" "$ROOT_DIR/plugins"; do
        [[ -d "$directory" ]] || continue
        while IFS= read -r -d '' file; do
            REQ_FILES+=("$file")
        done < <(find "$directory" -type f \( -name 'requirements.txt' -o -name '*_requirements.txt' \) -print0 | sort -z)
    done
}

requirements_fingerprint() {
    "$VENV_PYTHON" - "$BOOTSTRAP_VERSION" "${REQ_FILES[@]}" <<'PY'
import hashlib
import pathlib
import sys

version, *paths = sys.argv[1:]
digest = hashlib.sha256()
digest.update(f'bootstrap={version}\n'.encode())
for raw_path in paths:
    path = pathlib.Path(raw_path)
    digest.update(str(path).encode())
    digest.update(b'\0')
    digest.update(hashlib.sha256(path.read_bytes()).digest())
print(digest.hexdigest())
PY
}

core_dependencies_work() {
    "$VENV_PYTHON" -c 'import aiohttp, cryptography, dotenv, httpx, psutil, qcloud_cos, websockets, yaml' >/dev/null 2>&1
}

pip_install() {
    step '正在优先使用清华 PyPI 镜像安装依赖...'
    if "$VENV_PYTHON" -m pip install --disable-pip-version-check --index-url "$PIP_MIRROR" "$@"; then
        return
    fi

    step '镜像源安装失败，正在切换到官方 PyPI...'
    "$VENV_PYTHON" -m pip install --disable-pip-version-check --index-url "$OFFICIAL_PIP_SOURCE" "$@"
}

ensure_dependencies() {
    step '[4/6] 正在扫描框架、模块和插件的依赖文件...'
    collect_requirement_files
    (( ${#REQ_FILES[@]} > 0 )) || fail '未找到任何依赖文件。'
    step "[4/6] 已找到 ${#REQ_FILES[@]} 个依赖文件。"

    local fingerprint saved_fingerprint=''
    fingerprint="$(requirements_fingerprint)"
    if [[ -f "$STAMP_FILE" ]]; then
        saved_fingerprint="$(<"$STAMP_FILE")"
    fi
    if [[ "$saved_fingerprint" == "$fingerprint" ]] && core_dependencies_work; then
        step '[5/6] 依赖已经安装且为最新状态，无需重复安装。'
        return
    fi

    step "[5/6] 正在根据 ${#REQ_FILES[@]} 个依赖文件安装依赖..."
    "$VENV_PYTHON" -m ensurepip --upgrade >/dev/null 2>&1 || true
    pip_install --upgrade pip setuptools wheel

    local pip_arguments=()
    local requirement
    for requirement in "${REQ_FILES[@]}"; do
        pip_arguments+=(-r "$requirement")
    done
    pip_install "${pip_arguments[@]}"
    core_dependencies_work || fail '依赖安装已经结束，但仍有一个或多个核心包无法导入。'
    printf '%s\n' "$fingerprint" > "$STAMP_FILE"
    step '[5/6] 依赖安装完成并通过验证。'
}

get_configured_web_port() {
    "$VENV_PYTHON" - "$ROOT_DIR" <<'PY'
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
PY
}

local_port_open() {
    "$VENV_PYTHON" - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
for host in ('127.0.0.1', '::1'):
    try:
        with socket.create_connection((host, port), timeout=0.8):
            raise SystemExit(0)
    except OSError:
        pass
raise SystemExit(1)
PY
}

web_panel_available() {
    "$VENV_PYTHON" - "$1" <<'PY'
import sys
import urllib.request

port = int(sys.argv[1])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(f'http://127.0.0.1:{port}/web/', timeout=3) as response:
        raise SystemExit(0 if 200 <= response.status < 400 else 1)
except Exception:
    raise SystemExit(1)
PY
}

step '正在准备运行环境...'
ensure_virtual_environment
ensure_framework
ensure_dependencies

if (( SETUP_ONLY == 1 )); then
    step '[6/6] 已选择仅配置环境模式，跳过框架启动。'
    step '运行环境配置成功。'
    exit 0
fi

web_port=''
if ! web_port="$(get_configured_web_port)"; then
    fail '无法读取 Web 管理面板端口，请检查 config/settings.yaml。'
fi

step "Web 管理面板：http://localhost:${web_port}/web/"
step "[6/6] 正在检查配置端口 $web_port 是否已经开启..."
if local_port_open "$web_port"; then
    if web_panel_available "$web_port"; then
        step "[6/6] 检测到 ElainaBot 已在端口 $web_port 运行，无需重复启动。"
        exit 0
    fi
    fail "配置端口 $web_port 已被其他程序占用，但未检测到 ElainaBot 管理面板。"
fi

step "[6/6] 配置端口 $web_port 尚未开启，正在启动 ElainaBot 框架..."
exec "$VENV_PYTHON" "$ROOT_DIR/main.py"
