"""
用户-任务权限关联表
多对多关系：一个用户可以管理多个任务，一个任务可以被多个用户管理
"""
from app.extensions import db


class UserTaskPermission(db.Model):
    """
    用户任务权限关联表
    """
    __tablename__ = "user_task_permission"
    __allow_unmapped__ = True

    # 联合主键
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("sys_user.id", ondelete="CASCADE"),
        primary_key=True,
        comment="用户ID"
    )
    task_id = db.Column(
        db.Integer,
        db.ForeignKey("script_task.id", ondelete="CASCADE"),
        primary_key=True,
        comment="任务ID"
    )
    
    # 权限类型
    can_edit_script = db.Column(
        db.Boolean, default=True, nullable=False, comment="是否允许编辑脚本内容"
    )
    
    # 可选：添加创建时间
    created_at = db.Column(
        db.DateTime, 
        server_default=db.func.now(),
        comment="权限授予时间"
    )

    def __repr__(self) -> str:
        return (
            f"<UserTaskPermission user_id={self.user_id} "
            f"task_id={self.task_id} can_edit={self.can_edit_script}>"
        )
