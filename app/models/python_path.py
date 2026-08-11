"""
Python 解释器路径管理模型
存储用户配置的 Python 可执行文件路径，供任务管理和文件编辑器共享使用
"""
from datetime import datetime
from typing import Optional

from app.extensions import db


class PythonPath(db.Model):
    """
    Python 解释器路径表
    所有用户共享，支持设置默认路径
    """

    __tablename__: str = "python_path"
    __allow_unmapped__ = True

    # 主键
    id: int = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # Python 可执行文件路径
    path: str = db.Column(
        db.String(512), nullable=False, unique=True, comment="Python可执行文件路径"
    )
    # 是否为默认路径
    is_default: bool = db.Column(
        db.Boolean, default=False, nullable=False, comment="是否为默认路径"
    )
    # 备注
    description: Optional[str] = db.Column(
        db.String(256), default=None, nullable=True, comment="备注说明"
    )
    # 创建时间
    created_at: datetime = db.Column(
        db.DateTime(timezone=True), default=datetime.now, nullable=False,
        comment="创建时间"
    )

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "id": self.id,
            "path": self.path,
            "is_default": self.is_default,
            "description": self.description,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S")
            if self.created_at else None,
        }
