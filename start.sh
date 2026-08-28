#!/usr/bin/env bash
set -Eeuo pipefail

BOOTSTRAP_VERSION='5'
PYTHON_VERSION='3.13'
DEFAULT_PYTHON_INSTALL_MIRROR='https://registry.npmmirror.com/-/binary/python-build-standalone'
PYTHON_INSTALL_MIRROR="${ELAINABOT_PYTHON_MIRROR:-$DEFAULT_PYTHON_INSTALL_MIRROR}"
PYTHON_INSTALL_MIRROR="${PYTHON_INSTALL_MIRROR%/}"
PIP_MIRROR="${ELAINABOT_PIP_MIRROR:-https://pypi.tuna.tsinghua.edu.cn/simple}"
OFFICIAL_PIP_SOURCE="${ELAINABOT_OFFICIAL_PIP_SOURCE:-https://pypi.org/simple}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
TOOLS_DIR="$ROOT_DIR/.bootstrap/uv"
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

python_is_preferred() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)' >/dev/null 2>&1
}

find_preferred_python() {
    local candidate
    for candidate in python3.13 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1 && python_is_preferred "$(command -v "$candidate")"; then
            command -v "$candidate"
            return 0
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
    mkdir -p "$TOOLS_DIR"
    local installer
    installer="$(mktemp "${TMPDIR:-/tmp}/elainabot-uv-install.XXXXXX")"
    trap 'rm -f "$installer"' RETURN
    if command -v curl >/dev/null 2>&1; then
        curl --proto '=https' --tlsv1.2 -LsSf https://astral.sh/uv/install.sh -o "$installer"
    else
        wget -qO "$installer" https://astral.sh/uv/install.sh
    fi
    UV_INSTALL_DIR="$TOOLS_DIR" UV_NO_MODIFY_PATH=1 sh "$installer" >&2
    rm -f "$installer"
    trap - RETURN
    [[ -x "$local_uv" ]] || fail '无法安装项目专用的 Python 环境引导工具。'
    uv_supports_python_mirror "$local_uv" || fail '项目专用的 Python 环境引导工具不支持镜像下载，请稍后重试。'
    printf '%s\n' "$local_uv"
}

install_managed_python() {
    local uv_bin="$1"

    step "[1/5] 正在通过镜像下载项目专用的 Python $PYTHON_VERSION（将显示实时进度）..."
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
    [[ -e "$VENV_DIR" ]] || return
    local backup="$ROOT_DIR/.venv.backup-$(date +%Y%m%d-%H%M%S)"
    step "现有虚拟环境无效，正在将其移动到 ${backup##*/}。"
    mv -- "$VENV_DIR" "$backup"
}

ensure_virtual_environment() {
    step '[1/5] 正在检查 Python 3.11 或更高版本...'
    if [[ -x "$VENV_PYTHON" ]] && python_is_compatible "$VENV_PYTHON"; then
        step "[1/5] Python 已就绪：$("$VENV_PYTHON" -c 'import platform; print(platform.python_version())')"
        step '[2/5] 已有虚拟环境可用：.venv'
        return
    fi

    backup_invalid_venv
    local python_bin=''
    if python_bin="$(find_preferred_python)"; then
        step "[1/5] 已找到 Python 3.13：$("$python_bin" -c 'import platform; print(platform.python_version())')"
        step '[2/5] 正在创建项目虚拟环境：.venv...'
        if "$python_bin" -m venv "$VENV_DIR" && [[ -x "$VENV_PYTHON" ]]; then
            step '[2/5] 虚拟环境创建成功。'
            return
        fi
        if [[ -e "$VENV_DIR" ]]; then
            mv -- "$VENV_DIR" "$ROOT_DIR/.venv.failed-$(date +%Y%m%d-%H%M%S)"
        fi
        step '系统 Python 无法创建虚拟环境，将改用项目专用的 Python。'
    fi

    local uv_bin
    uv_bin="$(ensure_uv)"
    install_managed_python "$uv_bin"
    step '[2/5] 正在创建项目虚拟环境：.venv...'
    "$uv_bin" --color always venv --python "$PYTHON_VERSION" \
        --managed-python --no-python-downloads "$VENV_DIR"
    [[ -x "$VENV_PYTHON" ]] || fail '虚拟环境创建结束，但未找到可用的 Python。'
    step '[2/5] 虚拟环境创建成功。'
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
    step '[3/5] 正在扫描框架、模块和插件的依赖文件...'
    collect_requirement_files
    (( ${#REQ_FILES[@]} > 0 )) || fail '未找到任何依赖文件。'
    step "[3/5] 已找到 ${#REQ_FILES[@]} 个依赖文件。"

    local fingerprint saved_fingerprint=''
    fingerprint="$(requirements_fingerprint)"
    if [[ -f "$STAMP_FILE" ]]; then
        saved_fingerprint="$(<"$STAMP_FILE")"
    fi
    if [[ "$saved_fingerprint" == "$fingerprint" ]] && core_dependencies_work; then
        step '[4/5] 依赖已经安装且为最新状态，无需重复安装。'
        return
    fi

    step "[4/5] 正在根据 ${#REQ_FILES[@]} 个依赖文件安装依赖..."
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
    step '[4/5] 依赖安装完成并通过验证。'
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
ensure_dependencies

if (( SETUP_ONLY == 1 )); then
    step '[5/5] 已选择仅配置环境模式，跳过框架启动。'
    step '运行环境配置成功。'
    exit 0
fi

web_port=''
if ! web_port="$(get_configured_web_port)"; then
    fail '无法读取 Web 管理面板端口，请检查 config/settings.yaml。'
fi

step "Web 管理面板：http://localhost:${web_port}/web/"
step "[5/5] 正在检查配置端口 $web_port 是否已经开启..."
if local_port_open "$web_port"; then
    if web_panel_available "$web_port"; then
        step "[5/5] 检测到 ElainaBot 已在端口 $web_port 运行，无需重复启动。"
        exit 0
    fi
    fail "配置端口 $web_port 已被其他程序占用，但未检测到 ElainaBot 管理面板。"
fi

step "[5/5] 配置端口 $web_port 尚未开启，正在启动 ElainaBot 框架..."
exec "$VENV_PYTHON" "$ROOT_DIR/main.py"
