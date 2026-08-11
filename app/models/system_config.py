"""
系统全局配置模型
存储短信接口、全局开关等系统级配置
"""
from datetime import datetime
from typing import Optional

from app.extensions import db


class SystemConfig(db.Model):
    """
    系统配置表（key-value 存储）
    用于存储全局配置，如短信接口、通知开关等
    """

    __tablename__: str = "system_config"
    __allow_unmapped__ = True

    id: int = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # 配置键（唯一）
    config_key: str = db.Column(
        db.String(128), unique=True, nullable=False, index=True, comment="配置键"
    )
    # 配置值（JSON 或纯文本）
    config_value: Optional[str] = db.Column(
        db.Text, default=None, nullable=True, comment="配置值"
    )
    # 配置说明
    description: Optional[str] = db.Column(
        db.String(256), default=None, nullable=True, comment="配置说明"
    )
    # 更新时间
    updated_at: datetime = db.Column(
        db.DateTime(timezone=True), default=datetime.now, onupdate=datetime.now,
        nullable=False, comment="更新时间"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "config_key": self.config_key,
            "config_value": self.config_value,
            "description": self.description,
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M:%S")
            if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<SystemConfig key={self.config_key}>"
