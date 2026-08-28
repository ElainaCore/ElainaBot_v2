# syntax=docker/dockerfile:1

# ElainaBot v2 - Docker image assembled from a main source checkout.
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置 pip 镜像源（加速国内下载）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制源码
COPY . .

# 备份默认配置、插件、模块（用于首次启动时复制到挂载卷）
RUN cp -r /app/config /app/config.defaults && \
    cp -r /app/plugins /app/plugins.defaults && \
    cp -r /app/modules /app/modules.defaults

# The reusable workflow stages this file under .ci before starting the build.
RUN install -m 0755 /app/.ci/entrypoint.sh /usr/local/bin/elainabot-entrypoint \
    && rm -rf /app/.ci

# 暴露 Web 面板端口（与 settings.yaml 中 server.port 一致）
EXPOSE 5200

# 入口脚本：首次启动自动复制默认配置
ENTRYPOINT ["/usr/local/bin/elainabot-entrypoint"]
