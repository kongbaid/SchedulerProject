"""
项目管理模型
保存用户常用的项目路径
"""
from datetime import datetime
from typing import Optional

from app.extensions import db


class SavedProject(db.Model):
    """
    已保存的项目路径表
    仅超级管理员可使用
    """

    __tablename__: str = "saved_project"
    __allow_unmapped__ = True

    # 主键
    id: int = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # 项目名称
    project_name: str = db.Column(
        db.String(128), nullable=False, comment="项目名称"
    )
    # 项目路径
    project_path: str = db.Column(
        db.String(512), nullable=False, unique=True, comment="项目路径"
    )
    # 备注
    description: Optional[str] = db.Column(
        db.Text, default=None, nullable=True, comment="备注说明"
    )
    # 创建时间
    created_at: datetime = db.Column(
        db.DateTime(timezone=True), default=datetime.now, nullable=False,
        comment="创建时间"
    )
    # 最后访问时间
    last_accessed_at: Optional[datetime] = db.Column(
        db.DateTime(timezone=True), default=None, nullable=True,
        comment="最后访问时间"
    )
    # 访问次数
    access_count: int = db.Column(
        db.Integer, default=0, nullable=False, comment="访问次数"
    )

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "id": self.id,
            "project_name": self.project_name,
            "project_path": self.project_path,
            "description": self.description,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
            "last_accessed_at": self.last_accessed_at.strftime("%Y-%m-%d %H:%M:%S") if self.last_accessed_at else None,
            "access_count": self.access_count,
        }
