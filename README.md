# SchedulerProject

> 给 **没有运维团队、没有 Git/CI 体系的内网小团队** 的、全 GUI 的 Python 脚本调度台。
> 零外网依赖 · 浏览器写代码 · 点一下就跑 · 有角色权限 · 有备份兜底。

[English](#english) · 中文

---

## 它是什么

一个基于 **Flask + APScheduler** 的 Web 系统，让你在浏览器里管理 Python / Shell 脚本的定时调度，
全程可视化操作，无需 Git、无需 IDE、无需搭 CI。

- 在线写、在线运行脚本（页面一键运行，流式查看输出）
- 定时调度（Cron / 间隔 / 一次性）
- 任务标签、任务依赖
- 日志查看 / 下载 / 统计
- 多用户 + 角色权限（admin / 普通用户）
- 内置备份管理（可视化配置备份目标，一键备份 / 恢复）
- 纯 Python 栈，无 JVM / ZooKeeper，部署轻

## 为什么会有这个项目（定位）

很多内网小团队（工厂、医院、政企科室、小企业）手上有几个 Python 脚本想定时跑、偶尔想在网页上改两笔，
但他们 **没有 GitLab、没有 CI、没有运维组**。

现有的开源方案在这块都很别扭：

| 方案 | 在内网小团队场景的尴尬 |
|---|---|
| 青龙 等脚本面板 | 默认"从 GitHub 拉仓库"，**纯内网下直接废掉**；也缺细粒度权限和任务依赖 |
| DolphinScheduler / Rundeck / XXL-JOB | 功能强，但要 JVM、要 ZooKeeper、要正经运维，小部门扛不动 |
| 企业级调度器 | 默认你"代码走 Git、不在网页改"，可内网常常根本没有 Git 体系 |

本项目填补的正是这块缝：**全 GUI 操作、纯内网可跑、部署轻（一个 Python + 一个 MySQL）、在线编辑和运行脚本**。
而且前端静态资源全部本地化（Bootstrap / CodeMirror / jQuery 在 `static/vendor/`），**不依赖任何 CDN，断网也能用**。

> ✅ 适用：想要"网页上点点就能管定时脚本"、又不便/不想出网的内网小团队。
> ❌ 不适用：已有成熟 GitLab + CI + 运维平台、需要 DAG 级大数据编排的团队（请用 DolphinScheduler）。

## 功能一览

| 功能 | 说明 |
|---|---|
| 可视化任务管理 | Web UI 对定时任务增删改查 |
| 调度引擎 | APScheduler：Cron / 间隔 / 一次性 |
| 在线代码编辑器 | 浏览器内 CodeMirror，文件树、实时输出、GBK 编码自动探测 |
| 在线运行 | 网页一键运行脚本，流式查看输出 |
| 任务标签 | 按标签分类管理 |
| 任务依赖 | 支持任务间依赖编排 |
| 日志系统 | 查看 / 下载 / 统计 |
| 多用户 + 角色权限 | admin / 普通用户；文件编辑器仅 admin 可访问 |
| 备份管理 | 可视化配置备份目标，一键备份 / 恢复 |
| 系统配置 | Web 界面管理系统参数 |

## 技术栈

Flask 2.3 · Flask-SQLAlchemy · Flask-Login · APScheduler · PyMySQL · MySQL
· Bootstrap 5 · CodeMirror · jQuery

## 快速开始（Docker，推荐）

```bash
git clone <your-repo-url>
cd SchedulerProject

# 方式一：直接用环境变量（推荐，无需本地 config.py）
docker compose up -d --build

# 方式二：先用模板生成配置再改
cp config_example.py config.py
# 编辑 config.py 填写数据库等
docker compose up -d --build
```

启动后访问 **http://\<你的IP\>:8082**。

> 默认管理员账号：`admin` / `admin123`，**请第一时间在用户管理里修改密码**。

### docker-compose 可覆盖的环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DB_PASSWORD` | `change-me` | MySQL root 密码（同时用于 app 连接） |
| `SECRET_KEY` | `please-change-this-secret-key` | Flask 会话密钥，生产务必改成随机值 |
| `DB_NAME` | `task_manager` | 数据库名（compose 会自动创建） |

应用容器内的数据库连接由 `docker-compose.yml` 的 `environment` 固定指向 `db` 服务，
其余配置项见下方「配置项」。

## 手动部署

```bash
# 1. 准备 Python 3.11 环境
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置
cp config_example.py config.py
# 编辑 config.py，填写 MySQL 地址 / 账号 / 密码

# 4.（可选）初始化数据库
# 应用启动时会自动建表并创建默认管理员，init_db.sql 仅含可选种子数据

# 5. 启动
python run.py --host 0.0.0.0 --port 8082
# 生产可用：gunicorn -w 1 -b 0.0.0.0:8082 run:app
```

## 配置项（环境变量，亦可在 config.py 中写死）

| 变量 | 默认值 | 说明 |
|---|---|---|
| `SECRET_KEY` | 占位符 | Flask 密钥，**生产必须改** |
| `DB_HOST` | `127.0.0.1` | MySQL 主机 |
| `DB_PORT` | `3306` | MySQL 端口 |
| `DB_USER` | `root` | MySQL 用户 |
| `DB_PASSWORD` | `change-me` | MySQL 密码 |
| `DB_NAME` | `task_manager` | 数据库名 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `TIMEZONE` | `Asia/Shanghai` | 时区 |

## 默认账号

| 用户名 | 密码 | 角色 |
|---|---|---|
| `admin` | `admin123` | 管理员（可访问文件编辑器） |

## 目录结构

```
SchedulerProject/
├── app/                  # Flask 应用（蓝图：auth/task/log/user/file_editor/backup）
├── static/vendor/        # 本地化前端资源（Bootstrap / CodeMirror / jQuery，无 CDN）
├── task/
│   └── script_template.py   # 任务脚本模板（已脱敏，可提交）
├── config_example.py     # 脱敏配置模板（复制为 config.py 使用）
├── init_db.sql           # 可选种子数据
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

> 注：`config.py`、`redis_util.py`、`logs/`、`spider_login/`、`node_modules/` 等含敏感信息或私有模块，
> 已通过 `.gitignore` / `.dockerignore` 排除，**不会进入仓库或镜像**。

## 安全说明

- 前端静态资源全部本地化，**零外网 / CDN 依赖，可完全离线运行**。
- 提交到仓库的代码已做脱敏：不含任何真实数据库地址、账号、密码。
- 默认管理员密码 `admin123` 请务必修改。
- 生产环境 `SECRET_KEY` 务必通过环境变量设置随机强密钥。

## 与其它项目的关系

本项目是 JobCenter（Flask + APScheduler 思路）的强化超集：在其之上补齐了浏览器文件编辑器、
任务标签、任务依赖、RBAC 角色权限、备份管理。
与青龙等定位不同——本项目更偏「**内网小团队的全 GUI 脚本调度台**」，而非「脚本订阅托管平台」。

---

## English

### SchedulerProject — An all-GUI Python script scheduler for offline / intranet small teams

A lightweight **Flask + APScheduler** web app to manage and run timed Python/Shell scripts entirely from the browser —
no Git, no IDE, no CI required. Built for small intranet teams (factories, hospitals, gov/enterprise departments, SMBs)
that have a few scripts to schedule but no ops/Git infrastructure.

**Why this exists:** mainstream schedulers assume you have GitLab + CI + ops (DolphinScheduler/Rundeck/XXL-JOB — heavy, JVM/ZK),
while lightweight panels like QingLong assume outbound GitHub access and lack fine-grained RBAC / task dependencies.
This project fills the gap: **fully GUI, runs fully offline, deploys with just Python + MySQL, edits & runs scripts in-browser.**
All front-end assets are vendored locally under `static/vendor/` — **no CDN, works with zero internet**.

### Features

- Web UI CRUD for scheduled tasks (Cron / interval / one-shot via APScheduler)
- In-browser code editor (CodeMirror) with file tree, live output, GBK encoding auto-detection
- Run scripts in-browser with streamed output
- Task tags and task dependencies
- Log viewing / download / statistics
- Multi-user with roles (admin / user); file editor restricted to admin
- Built-in backup management (configure targets, one-click backup / restore)
- Pure Python — no JVM / ZooKeeper

### Quick start (Docker)

```bash
git clone <your-repo-url>
cd SchedulerProject
docker compose up -d --build
# open http://<your-ip>:8082
# default admin: admin / admin123  (change it immediately)
```

### Manual

```bash
pip install -r requirements.txt
cp config_example.py config.py      # edit DB settings
python run.py --host 0.0.0.0 --port 8082
```

Default admin: `admin` / `admin123`. All secrets are env-driven; no real credentials are committed.

### License

[MIT](./LICENSE)
