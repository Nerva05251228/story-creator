# Story Creator

本仓库是本地 `text2image2video_20260310` 项目的代码备份，用于后续重构。仓库会保留代码、启动脚本、迁移脚本、测试和硬编码配置；运行数据、数据库文件、生成图片/视频、虚拟环境和缓存不会上传。

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

## 数据库

当前启动脚本默认连接本机 PostgreSQL：

```text
postgresql://postgres:123456@127.0.0.1:5432/story_creator_20260310
```

在新机器上需要先创建这个数据库。任选一种方式：

```powershell
createdb -h 127.0.0.1 -U postgres story_creator_20260310
```

或：

```powershell
psql -h 127.0.0.1 -U postgres -c "CREATE DATABASE story_creator_20260310;"
```

如果本机 PostgreSQL 的 `postgres` 密码不是 `123456`，需要同步修改 `start_web.ps1`、`start_poller.ps1`、`start_all.ps1` 和 `start_server.cmd` 里的 `DATABASE_URL`。

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

这些配置当前多数已经硬编码在代码或启动脚本里；如果换机器后服务不可用，优先检查对应环境变量或默认值。

- `DATABASE_URL`：数据库连接串，启动脚本已设置。
- `BANANA_IMAGE_API_TOKEN`：图片服务 Token，PowerShell 启动脚本已设置。
- `TEXT_RELAY_BASE_URL` / `LLM_RELAY_BASE_URL`：文本模型中转服务地址。
- `TEXT_RELAY_API_KEY` / `LLM_RELAY_API_KEY`：文本模型中转服务 Key。
- `SORA_VIDEO_API_BASE_URL` / `VIDEO_API_BASE_URL`：视频服务地址。
- `SORA_VIDEO_API_TOKEN` / `VIDEO_API_TOKEN`：视频服务 Key。
- `IMAGE_PLATFORM_BASE_URL` / `IMAGE_PLATFORM_API_TOKEN` / `IMAGE_SERVICE_API_KEY`：图片平台相关配置。
- `VOICEOVER_TTS_API_URL`：配音/TTS 服务地址。
- `PORT`：Web 端口，默认 `10001`。
- `WEB_CONCURRENCY`：Web worker 数量，启动脚本默认 `4`。
- `ENABLE_BACKGROUND_POLLER`：是否启用后台轮询，Web 脚本为 `0`，Poller 脚本为 `1`。

## 没有上传的内容

为了避免把数据部分推到 GitHub，以下内容由 `.gitignore` 排除：

- `venv/`
- `uploads/`、`videos/`
- `backend/uploads/`、`backend/videos/`、`backend/stitched_images/`
- `backend/exports/`、`backend/ai_debug/`、`ai_debug/`
- `*.db`、`*.sqlite*`
- `backend/migration_backups/`、`backend/migration_reports/`
- `__pycache__/`、`.pytest_cache/`
- `.claude/`、`backend/.claude/`

克隆后如果需要恢复历史业务数据，需要单独复制数据库、上传文件、生成图片和视频目录。
