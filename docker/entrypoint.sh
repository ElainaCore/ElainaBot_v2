#!/bin/bash
set -e

# 如果挂载的 config 目录为空，复制示例配置 (Python ConfigManager 会在首次加载时自动从 .example.yaml 生成 .yaml)
if [ ! -f /app/config/settings.yaml ] && [ ! -f /app/config/settings.example.yaml ]; then
    echo ">>> 首次启动，复制示例配置..."
    mkdir -p /app/config
    cp /app/config.defaults/*.example.yaml /app/config/ 2>/dev/null || true
fi

# 如果挂载的 plugins 目录为空，复制系统插件
if [ ! -d /app/plugins/system ]; then
    echo ">>> 首次启动，复制默认插件..."
    mkdir -p /app/plugins
    cp -r /app/plugins.defaults/* /app/plugins/ 2>/dev/null || true
fi

# 如果挂载的 modules 目录为空，复制默认模块
if [ -z "$(ls -A /app/modules 2>/dev/null)" ]; then
    echo ">>> 首次启动，复制默认模块..."
    mkdir -p /app/modules
    cp -r /app/modules.defaults/* /app/modules/ 2>/dev/null || true
fi

echo ">>> 启动 ElainaBot..."
exec python main.py "$@"
