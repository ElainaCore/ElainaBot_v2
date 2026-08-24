<p>
<img src="https://download.nature.qq.com/SnsShare/SocialProfile/1779098988_1264b08a.png" width="200" align="left" style="border-radius:50%; margin-right:16px" />

<h1>ElainaBot v2</h1>

ElainaBot v2 是一个基于 Python 的 QQ 官方机器人框架，采用纯异步架构，支持 Webhook / WebSocket 多机器人连接、插件热重载、模块化扩展和 Web 面板管理。

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)](https://python.org) [![License](https://img.shields.io/badge/License-MIT-green)](LICENSE) [![QQ群](https://img.shields.io/badge/QQ交流群-1085402468-blue)](https://qm.qq.com/q/5O3xGoe4so)

- **纯异步架构** — 基于 aiohttp / websockets，高并发低延迟
- **插件市场** — 基于 GitHub 插件库，一键浏览、安装、更新插件
- **Web 管理面板** — 实时日志、系统监控、插件管理、配置编辑、数据库浏览

</p>
<br clear="left" />

> 项目仅供学习交流使用，严禁用于非法行为。交流群：[164178653](https://qm.qq.com/q/iNI4IyQdqw)。

## 🚀 快速开始

要求：Python 3.11+、Git。

```bash
git clone https://github.com/ElainaCore/ElainaBot_v2.git
cd ElainaBot_v2
pip install -r requirements.txt
python main.py
```

启动后访问 [Web 面板](http://localhost:5200/web/) 完成配置，默认密码为 `admin`。Webhook 回调地址可在面板中点击机器人名称右侧的感叹号图标查看。

## 📁 框架结构

```
ElainaBot_v2/
├── main.py          # 主程序入口
├── config/          # 配置文件
├── core/            # 核心框架 (网络、消息、插件、存储)
├── plugins/         # 插件目录 (热加载)
├── modules/         # 模块目录
├── web/             # Web 面板后端
├── docker/          # Docker 构建与 Compose 配置
└── docs/            # 开发文档
```

## 🔌 开发与扩展

- **开发文档** — [文档目录](docs/README.md)；其中[插件开发文档](docs/plugin-development.md)包含插件结构、事件处理、消息 API、入群审批、群禁言和 Web 面板扩展。
- **图床模块** — 通过 `get_app().module_manager.get("image_hosting")` 获取；公开 API、配置和示例见 [Image Hosting 文档](docs/image-hosting.md)。

## 🛒 插件市场

框架从 [Elaina-plugins](https://github.com/ElainaCore/Elaina-plugins) 获取插件列表，支持在 Web 面板中浏览、搜索、一键安装和镜像加速下载；插件开发者可向该仓库提交 PR，将插件加入市场。

## 🤝 反馈与贡献

遇到问题或有功能建议，请前往 [Issues](https://github.com/ElainaCore/ElainaBot_v2/issues) 提交 Issue；欢迎通过 [Pull Requests](https://github.com/ElainaCore/ElainaBot_v2/pulls) 提交 PR，参与项目改进。

## 🐳 Docker 一键部署

要求：[Docker](https://docs.docker.com/get-docker/) 20.10+、[Docker Compose](https://docs.docker.com/compose/install/) v2+。推荐直接拉取预构建镜像，无需克隆代码。

**docker compose（推荐）**

```bash
mkdir -p elainabot/docker && cd elainabot
curl -o docker/compose.yml https://raw.githubusercontent.com/ElainaCore/ElainaBot_v2/main/docker/compose.yml
docker compose -f docker/compose.yml up -d
```

**docker run**

```bash
docker run -d \
  --name elainabot \
  -p 5200:5200 \
  -v ./config:/app/config \
  -v ./plugins:/app/plugins \
  -v ./modules:/app/modules \
  -v ./data:/app/data \
  --restart unless-stopped \
  elainabot/elainabot:latest
```

启动后访问 [Web 面板](http://localhost:5200/web/?token=admin)，填写机器人的 `APPID` 和 `Secret`。

### 数据持久化

以下目录已通过 Volume 挂载到宿主机，容器删除后数据不会丢失：

| 目录 | 说明 |
|------|------|
| `./config/` | 机器人配置文件 |
| `./plugins/` | 已安装的插件 |
| `./modules/` | 模块文件 |
| `./data/` | 数据库、日志、媒体等运行数据 |

### 自行构建（可选）

```bash
git clone https://github.com/ElainaCore/ElainaBot_v2.git
cd ElainaBot_v2
docker compose -f docker/compose.build.yml up -d --build
```

也可以直接构建镜像：

```bash
docker build -f docker/Dockerfile -t elainabot/elainabot:local .
```
