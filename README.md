# ElainaBot CI 资源

这是一个独立的孤儿分支，用于在应用源代码分支之外保存测试套件和 Docker
部署资源。

- `tests/`：可复用 CI 工作流使用的测试套件。
- `docker/`：Dockerfile、启动脚本和 Compose 配置。
- `.github/workflows/`：由 `main` 分支中精简的工作流入口调用的可复用工作流。

工作流会先检出触发 `main` 调用方的准确源代码提交，再加入本分支中的资源。
请保持分支名称 `ci-assets` 稳定，并启用分支保护以防止误删。

如需在本地构建源代码镜像，请先克隆 `main`，再将 `docker/` 中的四个文件复制到该
检出目录的 `.ci/` 文件夹，最后运行：

```bash
docker compose -f .ci/compose.build.yml up -d --build
```
