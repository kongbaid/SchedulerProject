"""
路由包初始化
注册所有蓝图
"""
from app.routes.auth import auth_bp
from app.routes.task import task_bp
from app.routes.log import log_bp
from app.routes.file_editor import file_editor_bp
from app.routes.backup import backup_bp
