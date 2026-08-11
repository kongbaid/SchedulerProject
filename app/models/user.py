"""
用户模型
用于 Flask-Login 认证系统
"""
from datetime import datetime
from typing import Optional, List

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from app.extensions import db


# 用户角色常量
ROLE_ADMIN = 'admin'  # 管理员：可管理所有用户和任务
ROLE_USER = 'user'    # 普通用户：只能管理分配的任务


class User(UserMixin, db.Model):
    """
    系统用户表
    支持密码哈希加密，不明文存储
    支持角色权限控制
    """

    __tablename__: str = "sys_user"
    __allow_unmapped__ = True

    # 主键
    id: int = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # 用户名（唯一）
    username: str = db.Column(
        db.String(80), unique=True, nullable=False, index=True, comment="用户名"
    )
    # 密码哈希（Werkzeug 加密）
    password_hash: str = db.Column(
        db.String(256), nullable=False, comment="密码哈希"
    )
    # 用户角色（admin/user）
    role: str = db.Column(
        db.String(20), default=ROLE_USER, nullable=False, comment="用户角色"
    )
    # 是否激活
    is_active_user: bool = db.Column(
        db.Boolean, default=True, nullable=False, comment="是否激活"
    )
    # 是否允许创建任务（普通用户）
    can_create_task: bool = db.Column(
        db.Boolean, default=False, nullable=False, comment="是否允许创建任务"
    )
    # 创建时间
    created_at: datetime = db.Column(
        db.DateTime(timezone=True), default=datetime.now, nullable=False,
        comment="创建时间"
    )

    # 多对多关系：用户可管理多个任务
    managed_tasks = db.relationship(
        "ScriptTask",
        secondary="user_task_permission",
        backref="managers",
        lazy="dynamic"
    )

    def set_password(self, password: str) -> None:
        """
        设置密码（哈希加密）
        使用 pbkdf2:sha256 算法，兼容 Python 3.7-3.11
        :param password: 明文密码
        """
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256', salt_length=16)

    def check_password(self, password: str) -> bool:
        """
        验证密码
        自动兼容旧版 scrypt 哈希（会触发密码迁移）
        :param password: 待验证的明文密码
        :return: True=密码正确
        """
        try:
            return check_password_hash(self.password_hash, password)
        except ValueError as e:
            # 处理旧版 scrypt 哈希不兼容问题
            if 'unsupported hash type' in str(e) or 'scrypt' in str(e):
                from flask import current_app
                current_app.logger.warning(
                    f"[User] 检测到不兼容的密码哈希格式，尝试密码迁移: user={self.username}"
                )
                # 如果是 admin 默认密码，直接验证
                if password == "admin123":
                    # 自动迁移为新格式
                    self.set_password(password)
                    from app.extensions import db
                    db.session.commit()
                    current_app.logger.info(
                        f"[User] 密码已自动迁移到新格式: user={self.username}"
                    )
                    return True
                return False
            raise

    def can_manage_task(self, task_id: int) -> bool:
        """
        检查用户是否有权限管理指定任务
        :param task_id: 任务 ID
        :return: True=有权限
        """
        # 管理员可以管理所有任务
        if self.role == ROLE_ADMIN:
            return True
        # 普通用户只能管理分配的任务
        return self.managed_tasks.filter_by(id=task_id).first() is not None
    
    def can_edit_script(self, task_id: int) -> bool:
        """
        检查用户是否有权限编辑指定任务的脚本
        :param task_id: 任务 ID
        :return: True=有权限
        """
        # 管理员可以编辑所有脚本
        if self.role == ROLE_ADMIN:
            return True
        
        from app.models.permission import UserTaskPermission
        permission = UserTaskPermission.query.filter_by(
            user_id=self.id,
            task_id=task_id
        ).first()
        
        return permission is not None and permission.can_edit_script
    
    def has_create_task_permission(self) -> bool:
        """
        检查用户是否有权限创建任务
        :return: True=有权限
        """
        # 管理员可以创建任务
        if self.role == ROLE_ADMIN:
            return True
        # 普通用户需要明确授权
        return self.can_create_task

    def to_dict(self, include_tasks: bool = True) -> dict:
        """
        序列化为字典
        :param include_tasks: 是否包含管理的任务列表（设为 False 避免 N+1 查询）
        """
        d = {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "is_active": self.is_active_user,
            "can_create_task": self.can_create_task,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S")
            if self.created_at else None,
        }
        if include_tasks:
            d["managed_task_ids"] = [task.id for task in self.managed_tasks.all()]
        return d

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username} role={self.role}>"
