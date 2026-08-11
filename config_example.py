"""
应用配置模块（GitHub 模板）
=================================
使用前请复制本文件为 config.py：

    cp config_example.py config.py

然后按需修改下面的数据库等配置。
所有敏感值都优先读取环境变量，未设置时使用下面的本地默认值，
默认值为安全占位符，请勿在生产直接使用。

包含数据库、调度器、日志等生产级配置。
"""
import os
from typing import Optional
from urllib.parse import quote_plus

# 项目根目录
BASE_DIR: str = os.path.abspath(os.path.dirname(__file__))


class Config:
    """Flask 应用生产配置（示例）"""

    # ========== Flask 核心配置 ==========
    # 生产环境务必通过环境变量设置一个随机强密钥
    SECRET_KEY: str = os.environ.get(
        "SECRET_KEY", "flask-task-manager-secret-key-change-in-production"
    )

    # ========== MySQL 数据库配置 ==========
    # 真实值请通过环境变量注入，或在本文件填写；切勿提交含真实口令的 config.py
    DB_HOST: str = os.environ.get("DB_HOST", "127.0.0.1")
    DB_PORT: int = int(os.environ.get("DB_PORT", "3306"))
    DB_USER: str = os.environ.get("DB_USER", "root")
    DB_PASSWORD: str = os.environ.get("DB_PASSWORD", "change-me")
    DB_NAME: str = os.environ.get("DB_NAME", "task_manager")

    # 构建连接字符串（使用 PyMySQL 驱动）
    SQLALCHEMY_DATABASE_URI: str = (
        f"mysql+pymysql://{DB_USER}:{quote_plus(DB_PASSWORD)}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
        f"?charset=utf8mb4"
    )

    # SQLAlchemy 连接池配置（生产级）
    SQLALCHEMY_ENGINE_OPTIONS: dict = {
        "pool_size": 10,           # 连接池大小
        "max_overflow": 20,        # 最大溢出连接数
        "pool_recycle": 3600,      # 连接回收时间（秒），防止 MySQL gone away
        "pool_pre_ping": True,     # 每次获取连接前检测可用性（自动重连）
        "pool_timeout": 30,        # 获取连接超时时间（秒）
        "echo": False,             # 生产环境关闭 SQL 日志
    }

    # 禁用 Flask-SQLAlchemy 的事件通知（减少内存开销）
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False

    # ========== APScheduler 配置 ==========
    # 调度器使用独立数据库连接（与 Flask-SQLAlchemy 隔离）
    SCHEDULER_DB_URI: str = SQLALCHEMY_DATABASE_URI

    # ========== 日志配置 ==========
    LOG_DIR: str = os.path.join(BASE_DIR, "logs")
    LOG_FILE: str = os.path.join(LOG_DIR, "app.log")
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

    # ========== 文件锁配置（多进程安全）==========
    LOCK_FILE_PATH: str = os.path.join(BASE_DIR, ".scheduler.lock")

    # ========== 任务执行配置 ==========
    # 日志自动清理天数
    LOG_RETENTION_DAYS: int = 3
    # 日志清理定时（每天凌晨 02:00）
    CLEANUP_HOUR: int = 2
    CLEANUP_MINUTE: int = 0

    # ========== 文件编辑器配置 ==========
    # 文件树最大递归深度（防止深层目录导致性能问题）
    FILE_TREE_MAX_DEPTH: int = int(os.environ.get("FILE_TREE_MAX_DEPTH", "10"))
    # 脚本运行输出最大保留字节数（超出部分截断，防止内存溢出）
    EDITOR_MAX_OUTPUT_BYTES: int = int(os.environ.get("EDITOR_MAX_OUTPUT_BYTES", str(200 * 1024)))
    # 脚本运行超时秒数
    EDITOR_SCRIPT_TIMEOUT: int = int(os.environ.get("EDITOR_SCRIPT_TIMEOUT", "3000"))
    # 脚本运行期间数据库刷新间隔秒数（运行期间定期保存输出到数据库，防止崩溃丢失）
    EDITOR_DB_FLUSH_INTERVAL: int = int(os.environ.get("EDITOR_DB_FLUSH_INTERVAL", "30"))
    # 单用户最大同时运行脚本数（防止资源耗尽）
    EDITOR_MAX_CONCURRENT_SCRIPTS: int = int(os.environ.get("EDITOR_MAX_CONCURRENT_SCRIPTS", "5"))
    # 打开文件最大大小（字节），默认1MB
    EDITOR_MAX_FILE_SIZE: int = int(os.environ.get("EDITOR_MAX_FILE_SIZE", str(1 * 1024 * 1024)))

    # ========== Flask-Login 配置 ==========
    LOGIN_DISABLED: bool = False

    # ========== 时区配置 ==========
    # 统一使用本地时区存储时间
    TIMEZONE: str = "Asia/Shanghai"
