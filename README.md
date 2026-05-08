<div align="center">

# ElainaBot v2

ElainaBot 是一个基于 Python 的 QQ 官方机器人框架，采用纯异步架构，支持 Webhook / WebSocket 多机器人连接，  
插件热重载、模块化扩展、Web 面板管理等特性。

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![QQ群](https://img.shields.io/badge/QQ交流群-1085402468-blue)](https://qm.qq.com/q/5O3xGoe4so)

</div>

## ✨ 特性

- **纯异步架构** — 基于 aiohttp / websockets，高并发低延迟
- **插件市场** — 基于 GitHub 插件库，一键浏览、安装、更新插件
- **Web 管理面板** — 实时日志、系统监控、插件管理、配置编辑、数据库浏览

> 项目仅供学习交流使用，严禁用于任何商业用途和非法行为。

## 📢 交流群

**ElainaBot 框架交流群：[1085402468](https://qm.qq.com/q/5O3xGoe4so)**

## 🚀 快速开始

### 环境要求

- Python 3.10+
- Git

### 安装

```bash
git clone https://github.com/ElainaCore/ElainaBot_v2.git
cd ElainaBot_v2
pip install -r requirements.txt
python main.py
```

启动后访问 Web 面板完成配置：

```
http://localhost:5200/web/
```

## 📁 框架结构

```
ElainaBot_v2/
├── main.py                      # 主程序入口
├── requirements.txt             # 依赖包
├── config/                      # 配置文件目录
│   └── settings.yaml            # 主配置
├── core/                        # 核心框架
│   ├── bot.py                   # Bot 主类 (HTTP/WS 服务)
│   ├── base/                    # 基础设施 (配置、日志)
│   ├── network/                 # 网络层 (API 调用、鉴权)
│   ├── message/                 # 消息处理 (事件、发送、模板)
│   ├── plugin/                  # 插件系统 (加载、装饰器、上下文)
│   ├── module/                  # 模块系统
│   └── storage/                 # 存储层 (SQLite)
├── plugins/                     # 插件目录
│   └── system/                  # 内置系统插件
│       ├── main.py              # 入口 (含 __plugin_meta__)
│       └── app/                 # 子模块 (basic/admin/examples)
├── modules/                     # 模块目录
├── web/                         # Web 面板后端 (aiohttp)
│   ├── setup.py                 # 面板挂载入口
│   ├── api.py                   # API 路由
│   ├── auth.py                  # JWT 认证
│   ├── ws.py                    # WebSocket 推送
│   └── tools/                   # 工具模块
└── web-vue/                     # Web 面板前端 (Vue 3)
    ├── frontend/                # 源码
    └── dist/                    # 编译产物
```

## 🔌 插件开发

### 小型插件 (单文件)

在 `plugins/` 下创建目录，放入 `.py` 文件即可：

```python
# plugins/hello/hello.py
from core.plugin.decorators import handler

__plugin_meta__ = {
    'name': '打招呼',
    'author': '你的名字',
    'description': '简单的打招呼插件',
    'version': '1.0.0',
    'github': 'https://github.com/你的用户名/你的仓库',
}

@handler(r'^你好$', name='你好', desc='打招呼')
async def hello(event, match):
    await event.reply("你好！我是 ElainaBot 🎉")
```

### 大型插件 (入口 + 子模块)

在 `plugins/` 下创建目录，包含 `main.py` 入口文件：

```python
# plugins/my_plugin/main.py
from core.plugin.decorators import on_load, on_unload

__plugin_meta__ = {
    'name': '我的插件',
    'author': '你的名字',
    'description': '功能描述',
    'version': '1.0.0',
    'github': 'https://github.com/你的用户名/你的仓库',
}

from plugins.my_plugin.app import feature_a  # noqa
from plugins.my_plugin.app import feature_b  # noqa

@on_load
def _on_load():
    print("插件已加载")
```

### `__plugin_meta__` 字段

| 字段 | 说明 |
|------|------|
| `name` | 插件显示名 |
| `author` | 作者 |
| `description` | 简介 |
| `version` | 版本号 |
| `github` | GitHub 仓库地址 |
| `homepage` | 主页 URL |
| `license` | 开源协议 |

## 🛒 插件市场

框架内置插件市场，从 [ElainaCore/Elaina-plugins](https://github.com/ElainaCore/Elaina-plugins) 获取插件列表。

- **Web 面板** — 在线浏览、搜索、一键安装
- **镜像加速** — 自动使用最快 GitHub 镜像下载
- **两种安装模式**：
  - 仓库型：拉取整个 GitHub 仓库解压到 `plugins/<name>/`
  - 单文件型：从仓库中下载指定文件

**插件开发者** 请前往 [Elaina-plugins](https://github.com/ElainaCore/Elaina-plugins) 提交 PR，将你的插件加入市场。

## 🐳 Docker 一键部署

### 环境要求

- [Docker](https://docs.docker.com/get-docker/) 20.10+
- [Docker Compose](https://docs.docker.com/compose/install/) v2+

### 快速启动

**1. 克隆仓库**

```bash
git clone https://github.com/ElainaCore/ElainaBot_v2.git
cd ElainaBot_v2
```

**2. 构建并启动**

```bash
docker compose up -d --build
```

**3. 访问 Web 面板完成配置**

```
http://localhost:5200/web/?token=admin
```

在面板中填写机器人的 `APPID` 和 `Secret` 后即可正常运行。

### 常用命令

```bash
# 查看实时日志
docker compose logs -f

# 停止
docker compose down

# 重启
docker compose restart

# 更新代码后重新构建
docker compose up -d --build
```

### 数据持久化说明

以下目录已通过 Volume 挂载到宿主机，容器删除后数据不会丢失：

| 目录 | 说明 |
|------|------|
| `./config/` | 机器人配置文件 |
| `./plugins/` | 已安装的插件 |
| `./modules/` | 模块文件 |
| `./log/` | 运行日志 |

---

## 📄 License

MIT License
