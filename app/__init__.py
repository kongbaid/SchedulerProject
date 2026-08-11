"""
Flask 应用工厂
初始化所有组件：数据库、认证、调度器、日志系统
"""
import os
import sys
import atexit
import logging
from logging.handlers import RotatingFileHandler
from typing import Optional

from flask import Flask, redirect, url_for

from config import Config
from app.extensions import db, login_manager

# ============================================================
# 全局 Flask app 实例引用
# 供调度器线程、后台任务获取 app_context
# ============================================================
_app_instance: Optional[Flask] = None


def create_app(config_class=Config) -> Flask:
    """
    Flask 应用工厂
    :param config_class: 配置类
    :return: 初始化完成的 Flask app
    """
    global _app_instance

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="../static",
    )
    app.config.from_object(config_class)

    # ====== 1. 初始化日志系统 ======
    _setup_logging(app)

    # ====== 2. 初始化数据库 ======
    db.init_app(app)

    # ====== 3. 初始化 Flask-Login ======
    _setup_login(app)

    # ====== 4. 注册蓝图 ======
    _register_blueprints(app)

    # ====== 5. 创建数据库表 + 初始化默认管理员 ======
    with app.app_context():
        # 导入所有模型（确保 SQLAlchemy 能发现它们）
        from app.models import User, ScriptTask, TaskLog  # noqa: F401
        from app.models.permission import UserTaskPermission  # noqa: F401
        from app.models.task import TaskTag, TaskTagMapping, TaskDependency  # noqa: F401
        from app.models.system_config import SystemConfig  # noqa: F401
        from app.models.backup import BackupTarget, BackupRecord  # noqa: F401
        db.create_all()
        _init_admin_user(app)

    # ====== 6. 启动时修复异常任务 ======
    with app.app_context():
        from app.services.executor import repair_stale_tasks
        repair_stale_tasks()

    # ====== 7. 初始化调度器（文件锁保护）======
    from app.services.scheduler import scheduler
    scheduler.init_scheduler(app)

    # ====== 8. 注册全局错误处理 ======
    _register_error_handlers(app)

    # 存储全局引用
    _app_instance = app

    # ====== 9. 注册优雅退出处理（worker 回收时清理调度器和运行中的脚本）======
    _register_shutdown_handler(app)

    app.logger.info("=" * 50)
    app.logger.info("Flask 任务管理系统启动成功")
    app.logger.info(f"调度器状态: {'主进程' if scheduler.is_master else '从进程（不启动调度器）'}")
    app.logger.info("=" * 50)

    return app


def _setup_logging(app: Flask) -> None:
    """配置日志系统：控制台 + 文件（轮转）"""
    log_dir: str = app.config.get("LOG_DIR", "logs")
    log_file: str = app.config.get("LOG_FILE", os.path.join(log_dir, "app.log"))
    log_level: str = app.config.get("LOG_LEVEL", "INFO")

    # 确保日志目录存在
    os.makedirs(log_dir, exist_ok=True)

    # 日志格式
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件处理器（轮转，单文件最大 10MB，保留 5 个备份）
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # 配置根日志器（捕获所有模块日志）
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    # 避免重复添加 handler
    if not root_logger.handlers:
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)

    # Flask 自身日志
    app.logger.handlers = []
    app.logger.propagate = True


def _setup_login(app: Flask) -> None:
    """配置 Flask-Login"""
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        """根据 ID 加载用户（Flask-Login 回调）"""
        from app.models.user import User
        try:
            return db.session.get(User, int(user_id))
        except Exception:
            return None

    # 未登录 AJAX 请求返回 401 JSON
    @login_manager.unauthorized_handler
    def unauthorized():
        from flask import request, jsonify
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"code": 401, "msg": "请先登录", "data": {}}), 401
        return redirect(url_for("auth.login_page"))


def _register_blueprints(app: Flask) -> None:
    """注册所有蓝图"""
    from app.routes.auth import auth_bp
    from app.routes.task import task_bp
    from app.routes.log import log_bp
    from app.routes.user import user_bp
    from app.routes.file_editor import file_editor_bp
    from app.routes.backup import backup_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(task_bp)
    app.register_blueprint(log_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(file_editor_bp)
    app.register_blueprint(backup_bp)


def _init_admin_user(app: Flask) -> None:
    """
    初始化默认管理员账号
    如果不存在 admin 用户，自动创建
    默认账号：admin / admin123
    """
    from app.models.user import User, ROLE_ADMIN
    try:
        admin = User.query.filter_by(username="admin").first()
        if not admin:
            admin = User(
                username="admin",
                role=ROLE_ADMIN,
                is_active_user=True
            )
            admin.set_password("admin123")
            db.session.add(admin)
            db.session.commit()
            app.logger.info("[Init] 默认管理员账号已创建: admin / admin123")
        else:
            app.logger.info("[Init] 管理员账号已存在，跳过初始化")
    except Exception as e:
        app.logger.error(f"[Init] 初始化管理员失败: {e}")
        db.session.rollback()


def _register_error_handlers(app: Flask) -> None:
    """注册全局错误处理器"""
    from app.utils.response import error_response

    @app.errorhandler(404)
    def not_found(e):
        return error_response("页面不存在", code=404)

    @app.errorhandler(500)
    def internal_error(e):
        return error_response("服务器内部错误", code=500)

    @app.errorhandler(405)
    def method_not_allowed(e):
        return error_response("请求方法不允许", code=405)


def _register_shutdown_handler(app: Flask) -> None:
    """
    注册优雅退出处理
    - 停止 APScheduler 调度器
    - 终止文件编辑器中所有运行中的脚本
    - 释放文件锁
    """
    def _on_shutdown():
        logger = app.logger

        # 1. 停止调度器
        try:
            from app.services.scheduler import scheduler
            if scheduler.is_running:
                scheduler.shutdown()
                logger.info("[Shutdown] 调度器已停止")
        except Exception as e:
            logger.error(f"[Shutdown] 停止调度器异常: {e}")

        # 2. 终止文件编辑器中运行中的脚本
        try:
            from app.routes.file_editor import _running_scripts, _scripts_lock
            from app.utils.process import kill_process_tree
            with _scripts_lock:
                for uid, state in list(_running_scripts.items()):
                    proc = state.get('proc')
                    if proc and state.get('status') == 'running':
                        try:
                            kill_process_tree(proc.pid)
                            logger.info(f"[Shutdown] 已终止编辑器脚本 PID={proc.pid}")
                        except Exception:
                            pass
                _running_scripts.clear()
        except Exception as e:
            logger.error(f"[Shutdown] 清理编辑器脚本异常: {e}")

        logger.info("[Shutdown] 优雅退出完成")

    atexit.register(_on_shutdown)
