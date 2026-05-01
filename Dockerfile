# ElainaBot v2 - Docker 镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置 pip 镜像源（加速国内下载）
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY . .

# 暴露 Web 面板端口（与 settings.yaml 中 server.port 一致）
EXPOSE 5200

# 启动
CMD ["python", "main.py"]
