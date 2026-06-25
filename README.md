<p>
<img src="https://download.nature.qq.com/SnsShare/SocialProfile/1779098988_1264b08a.png" width="200" align="left" style="border-radius:50%; margin-right:16px" />

<h1>ElainaBot v2</h1>

ElainaBot V2是一个基于 Python 的 QQ 官方机器人框架，采用纯异步架构，支持 Webhook / WebSocket 多机器人连接，插件热重载、模块化扩展、Web 面板管理等特性。

[![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![QQ群](https://img.shields.io/badge/QQ交流群-1085402468-blue)](https://qm.qq.com/q/5O3xGoe4so)

- **纯异步架构** — 基于 aiohttp / websockets，高并发低延迟
- **插件市场** — 基于 GitHub 插件库，一键浏览、安装、更新插件
- **Web 管理面板** — 实时日志、系统监控、插件管理、配置编辑、数据库浏览

</p>
<br clear="left" />

> 项目仅供学习交流使用，严禁用于任何商业用途和非法行为。

## 📢 交流群

**ElainaBot 框架交流群：[1085402468](https://qm.qq.com/q/5O3xGoe4so)**

## 🚀 快速开始

### 环境要求

- Python 3.11+
- Git

### 安装

```bash
git clone https://github.com/ElainaCore/ElainaBot_v2.git #（手动部署跳过）
cd ElainaBot_v2
pip install -r requirements.txt
python main.py
```

启动后访问 Web 面板完成配置：

```
http://localhost:5200/web/
```

> **Webhook回调配置地址**: 进入框架后点击机器人名字右边的 **感叹号图标** 即可查看。

## 📁 框架结构

```
ElainaBot_v2/
├── main.py          # 主程序入口
├── config/          # 配置文件
├── core/            # 核心框架 (网络、消息、插件、存储)
├── plugins/         # 插件目录 (热加载)
├── modules/         # 模块目录
├── web/             # Web 面板后端
└── templates/       # 消息模板
```

## 🔌 插件开发

详见 **[插件开发文档 (PLUGIN_DEVELOPMENT.md)](PLUGIN_DEVELOPMENT.md)** — 包含完整的装饰器、Event API、按钮构造、主动消息、Web 面板扩展等参考。

## 🛒 插件市场

框架内置插件市场，从 [ElainaCore/Elaina-plugins](https://github.com/ElainaCore/Elaina-plugins) 获取插件列表。

- **Web 面板** — 在线浏览、搜索、一键安装
- **镜像加速** — 自动使用最快 GitHub 镜像下载
- **三种清单类型 (`type`)**：
  - `complete` 完整插件：解压整仓库或仓库内某子目录到 `plugins/<name>/`，支持一个仓库内放多个插件 (用 `path` 指向子目录)
  - `single` 独立插件：默认下载单文件到共享目录 `plugins/alone/<name>.py`；显式 `alone: false` 时改为装到专属目录 `plugins/<name>/`，支持多文件 (附带 html 等，用 `path` 指向子目录整目录下载)，彻底避免同名文件冲突
  - `module` 模块：解压到 `modules/<name>/`

**插件开发者** 请前往 [Elaina-plugins](https://github.com/ElainaCore/Elaina-plugins) 提交 PR，将你的插件加入市场。

## 🐳 Docker 一键部署

### 环境要求

- [Docker](https://docs.docker.com/get-docker/) 20.10+
- [Docker Compose](https://docs.docker.com/compose/install/) v2+

### 快速启动（推荐）

直接拉取预构建镜像，无需克隆代码：

**方式一：docker run**

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

**方式二：docker compose（推荐）**

```bash
mkdir elainabot && cd elainabot && curl -O https://raw.githubusercontent.com/ElainaCore/ElainaBot_v2/main/docker-compose.yml && docker compose up -d
```

**访问 Web 面板完成配置**

```
http://localhost:5200/web/?token=admin
```

在面板中填写机器人的 `APPID` 和 `Secret` 后即可正常运行。

### 数据持久化说明

以下目录已通过 Volume 挂载到宿主机，容器删除后数据不会丢失：

| 目录 | 说明 |
|------|------|
| `./config/` | 机器人配置文件 |
| `./plugins/` | 已安装的插件 |
| `./modules/` | 模块文件 |
| `./data/` | 数据库、日志、媒体等运行数据 |

### 自行构建（可选）

如需从源码构建：

```bash
git clone https://github.com/ElainaCore/ElainaBot_v2.git
cd ElainaBot_v2
docker compose -f docker-compose.build.yml up -d --build
```


