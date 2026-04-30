# Story Creator

本仓库是本地 `text2image2video_20260310` 项目的代码备份，用于后续重构。仓库会保留代码、启动脚本、迁移脚本、测试和公开配置模板；运行数据、数据库文件、生成图片/视频、虚拟环境、缓存和本地 `.env` 私有配置不会上传。

## 目录

- `backend/`：FastAPI 后端、数据库模型、迁移和后台轮询逻辑。
- `frontend/`：静态前端页面和 JS/CSS。
- `tests/`：现有测试。
- `start_web.ps1` / `start_poller.ps1` / `start_all.ps1`：Windows PowerShell 启动脚本。
- `start_server.cmd`：Windows CMD 版 Web 服务启动脚本。

## 新机器初始化

先安装这些基础依赖：

- Python 3.11 或兼容版本
- PostgreSQL
- Git

克隆仓库：

```powershell
git clone git@github.com:Nerva05251228/story-creator.git
cd story-creator
```

创建并安装 Python 虚拟环境：

```powershell
py -3.11 -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

复制环境变量模板并填写本机私有配置：

```powershell
Copy-Item .env.example .env
notepad .env
```

## 数据库

启动脚本会从本地 `.env` 读取 PostgreSQL 连接串：

```text
DATABASE_URL=postgresql://<user>@127.0.0.1:5432/story_creator_20260310
```

在新机器上需要先创建这个数据库和本地角色。下面命令使用 `.env.example` 中的占位用户名；如果你的 PostgreSQL 需要密码，请只在本机 `.env` 中填写带密码的 `DATABASE_URL`，不要提交。

```powershell
psql -h 127.0.0.1 -U postgres -c "CREATE ROLE story_creator_user LOGIN;"
psql -h 127.0.0.1 -U postgres -c "CREATE DATABASE story_creator_20260310 OWNER story_creator_user;"
```

如果角色已存在，只需要创建数据库：

```powershell
createdb -h 127.0.0.1 -U postgres -O story_creator_user story_creator_20260310
```

不要把真实数据库密码写入启动脚本或提交到仓库；只修改本地 `.env`。

首次启动时，`backend/preflight.py migrate` 会创建表结构并执行项目启动初始化。

## 启动

启动 Web 服务：

```powershell
.\start_web.ps1
```

启动后台轮询：

```powershell
.\start_poller.ps1
```

或者一次启动 Web 和 Poller：

```powershell
.\start_all.ps1
```

默认 Web 端口是：

```text
http://127.0.0.1:10001
```

前端是静态文件，后端会使用 `frontend/` 目录中的页面资源。

## 需要留意的配置

这些配置应写入本地 `.env`。仓库只保留 `.env.example` 占位符；不要提交真实密钥、私有接口地址或数据库密码。

- `DATABASE_URL`：数据库连接串。
- `REDIS_URL`：Redis 连接串，后续 worker/cache 重构使用。
- `TEXT_RELAY_BASE_URL` / `LLM_RELAY_BASE_URL`：文本模型中转服务地址。
- `TEXT_RELAY_API_KEY` / `LLM_RELAY_API_KEY`：文本模型中转服务 Key。
- `SORA_VIDEO_API_BASE_URL` / `VIDEO_API_BASE_URL`：视频服务地址。
- `SORA_VIDEO_API_TOKEN` / `VIDEO_API_TOKEN`：视频服务 Key。
- `IMAGE_PLATFORM_BASE_URL` / `IMAGE_PLATFORM_API_TOKEN` / `IMAGE_SERVICE_API_KEY` / `BANANA_IMAGE_API_TOKEN`：图片平台相关配置。
- `CDN_UPLOAD_URL` / `CDN_UPLOAD_PATH`：CDN 上传配置。
- `VOICEOVER_TTS_API_URL`：配音/TTS 服务地址。
- `MASTER_PASSWORD` / `ADMIN_PANEL_PASSWORD` / `DEFAULT_USER_PASSWORD`：本地认证相关私有值。
- `PORT`：Web 端口，默认 `10001`。
- `WEB_CONCURRENCY`：Web worker 数量，启动脚本默认 `4`。
- `ENABLE_BACKGROUND_POLLER`：是否启用后台轮询，Web 脚本为 `0`，Poller 脚本为 `1`。

## 没有上传的内容

为了避免把数据部分推到 GitHub，以下内容由 `.gitignore` 排除：

- `venv/`
- `.env`、`.env.*`（`.env.example` 除外）
- `uploads/`、`videos/`
- `backend/uploads/`、`backend/videos/`、`backend/stitched_images/`
- `backend/exports/`、`backend/ai_debug/`、`ai_debug/`
- `*.db`、`*.sqlite*`
- `backend/migration_backups/`、`backend/migration_reports/`
- `__pycache__/`、`.pytest_cache/`
- `.claude/`、`backend/.claude/`

克隆后如果需要恢复历史业务数据，需要单独复制数据库、上传文件、生成图片和视频目录。
