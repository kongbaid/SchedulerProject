"""
数据模型包初始化
导入所有模型，确保 SQLAlchemy 能发现它们
"""
from app.models.user import User
from app.models.task import ScriptTask, TaskTag, TaskTagMapping, TaskDependency
from app.models.log import TaskLog
from app.models.permission import UserTaskPermission
from app.models.project import SavedProject
from app.models.python_path import PythonPath
from app.models.system_config import SystemConfig
from app.models.editor_execution import EditorExecutionLog
from app.models.backup import BackupTarget, BackupRecord