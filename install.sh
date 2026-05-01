#!/bin/bash
set -e

echo ">>> 克隆 ElainaBot v2 仓库..."
git clone https://github.com/ElainaCore/ElainaBot_v2.git
cd ElainaBot_v2

echo ">>> 构建并启动 Docker 容器..."
docker compose up -d --build

echo ""
echo ">>> 部署完成！"
echo ">>> 访问面板: http://localhost:5200/web/?token=admin"
echo ">>> 查看日志: docker compose logs -f"
echo ">>> 停止服务: docker compose down"
