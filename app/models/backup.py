"""
备份模块数据模型
- BackupTarget: 备份目标目录配置
- BackupRecord: 备份记录
"""
from datetime import datetime
from typing import Optional

from app.extensions import db


class BackupTarget(db.Model):
    """备份目标目录配置表"""

    __tablename__: str = "backup_target"
    __allow_unmapped__ = True

    id: int = db.Column(db.Integer, primary_key=True, autoincrement=True)
    target_name: str = db.Column(
        db.String(128), nullable=False, comment="目录别名"
    )
    target_path: str = db.Column(
        db.String(512), nullable=False, unique=True, comment="备份存储路径"
    )
    description: Optional[str] = db.Column(
        db.Text, default=None, nullable=True, comment="备注"
    )
    is_default: int = db.Column(
        db.Integer, default=0, nullable=False, comment="是否默认备份目标 1/0"
    )
    created_by: Optional[str] = db.Column(
        db.String(64), default=None, nullable=True, comment="创建人"
    )
    created_at: datetime = db.Column(
        db.DateTime(timezone=True), default=datetime.now, nullable=False,
        comment="创建时间"
    )
    updated_at: Optional[datetime] = db.Column(
        db.DateTime(timezone=True), default=None, nullable=True,
        comment="更新时间"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target_name": self.target_name,
            "target_path": self.target_path,
            "description": self.description,
            "is_default": self.is_default,
            "created_by": self.created_by,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M:%S") if self.updated_at else None,
        }


class BackupRecord(db.Model):
    """备份记录表"""

    __tablename__: str = "backup_record"
    __allow_unmapped__ = True

    id: int = db.Column(db.Integer, primary_key=True, autoincrement=True)
    record_name: str = db.Column(
        db.String(256), nullable=False, comment="备份名称"
    )
    source_type: str = db.Column(
        db.String(20), nullable=False, comment="来源类型: project/folder/file"
    )
    source_path: str = db.Column(
        db.String(1024), nullable=False, comment="备份源路径"
    )
    source_name: str = db.Column(
        db.String(256), nullable=False, comment="源名称"
    )
    target_id: int = db.Column(
        db.Integer, db.ForeignKey("backup_target.id"), nullable=False,
        comment="备份目标目录ID"
    )
    target_path: str = db.Column(
        db.String(512), nullable=False, comment="冗余: 备份存储目录"
    )
    backup_file_name: str = db.Column(
        db.String(256), nullable=False, comment="备份文件名"
    )
    backup_file_path: str = db.Column(
        db.String(1024), nullable=False, comment="备份文件完整路径"
    )
    backup_size: int = db.Column(
        db.BigInteger, default=0, nullable=False, comment="文件大小(bytes)"
    )
    file_count: int = db.Column(
        db.Integer, default=0, nullable=False, comment="包含文件数"
    )
    status: str = db.Column(
        db.String(20), default="success", nullable=False,
        comment="success/failed"
    )
    error_message: Optional[str] = db.Column(
        db.Text, default=None, nullable=True, comment="失败原因"
    )
    created_by: Optional[str] = db.Column(
        db.String(64), default=None, nullable=True, comment="创建人"
    )
    created_at: datetime = db.Column(
        db.DateTime(timezone=True), default=datetime.now, nullable=False,
        comment="创建时间"
    )
    restored_at: Optional[datetime] = db.Column(
        db.DateTime(timezone=True), default=None, nullable=True,
        comment="最近恢复时间"
    )
    restore_count: int = db.Column(
        db.Integer, default=0, nullable=False, comment="恢复次数"
    )

    # 关联
    target = db.relationship("BackupTarget", backref="records")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "record_name": self.record_name,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "source_name": self.source_name,
            "target_id": self.target_id,
            "target_path": self.target_path,
            "backup_file_name": self.backup_file_name,
            "backup_file_path": self.backup_file_path,
            "backup_size": self.backup_size,
            "file_count": self.file_count,
            "status": self.status,
            "error_message": self.error_message,
            "created_by": self.created_by,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
            "restored_at": self.restored_at.strftime("%Y-%m-%d %H:%M:%S") if self.restored_at else None,
            "restore_count": self.restore_count,
        }
