"""
定时任务模型 ScriptTask
存储任务定义、cron 表达式、执行状态等信息
"""
from datetime import datetime
from typing import Optional, List

from app.extensions import db


class TaskTag(db.Model):
    """
    任务标签表
    用于给任务打标签分类（如 爬虫、财务、数据同步）
    """
    __tablename__: str = "task_tag"
    __allow_unmapped__ = True

    id: int = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name: str = db.Column(
        db.String(64), unique=True, nullable=False, index=True, comment="标签名称"
    )
    color: str = db.Column(
        db.String(20), default="#6c757d", nullable=False, comment="标签颜色(HEX)"
    )
    created_at: datetime = db.Column(
        db.DateTime(timezone=True), default=datetime.now, nullable=False, comment="创建时间"
    )

    # 多对多关系
    tasks = db.relationship(
        "ScriptTask",
        secondary="task_tag_mapping",
        backref="tags",
        lazy="dynamic",
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
        }

    def __repr__(self) -> str:
        return f"<TaskTag id={self.id} name={self.name}>"


class TaskTagMapping(db.Model):
    """任务-标签关联表"""
    __tablename__: str = "task_tag_mapping"
    __allow_unmapped__ = True

    task_id: int = db.Column(
        db.Integer,
        db.ForeignKey("script_task.id", ondelete="CASCADE"),
        primary_key=True,
        comment="任务ID",
    )
    tag_id: int = db.Column(
        db.Integer,
        db.ForeignKey("task_tag.id", ondelete="CASCADE"),
        primary_key=True,
        comment="标签ID",
    )


class TaskDependency(db.Model):
    """
    任务依赖关系表
    upstream_task_id 成功后自动触发 downstream_task_id
    支持 A -> B -> C 链式执行
    """
    __tablename__: str = "task_dependency"
    __allow_unmapped__ = True

    id: int = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # 上游任务（先执行）
    upstream_task_id: int = db.Column(
        db.Integer,
        db.ForeignKey("script_task.id", ondelete="CASCADE"),
        nullable=False,
        comment="上游任务ID（先执行）",
    )
    # 下游任务（后触发）
    downstream_task_id: int = db.Column(
        db.Integer,
        db.ForeignKey("script_task.id", ondelete="CASCADE"),
        nullable=False,
        comment="下游任务ID（后触发）",
    )
    # 是否启用
    is_active: bool = db.Column(
        db.Boolean, default=True, nullable=False, comment="是否启用"
    )
    created_at: datetime = db.Column(
        db.DateTime(timezone=True), default=datetime.now, nullable=False, comment="创建时间"
    )

    # 关系
    upstream_task = db.relationship("ScriptTask", foreign_keys=[upstream_task_id], backref="downstream_deps")
    downstream_task = db.relationship("ScriptTask", foreign_keys=[downstream_task_id], backref="upstream_deps")

    # 联合唯一约束
    __table_args__ = (
        db.UniqueConstraint("upstream_task_id", "downstream_task_id", name="uq_dep"),
        db.Index("idx_upstream", "upstream_task_id"),
        db.Index("idx_downstream", "downstream_task_id"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "upstream_task_id": self.upstream_task_id,
            "downstream_task_id": self.downstream_task_id,
            "upstream_task_name": self.upstream_task.task_name if self.upstream_task else "",
            "downstream_task_name": self.downstream_task.task_name if self.downstream_task else "",
            "is_active": self.is_active,
        }

    def __repr__(self) -> str:
        return (
            f"<TaskDependency upstream={self.upstream_task_id} "
            f"downstream={self.downstream_task_id}>"
        )


class ScriptTask(db.Model):
    """
    脚本定时任务表
    每个任务对应一个 Python 脚本文件和 cron 调度规则
    """

    __tablename__: str = "script_task"
    __allow_unmapped__ = True

    # 主键自增
    id: int = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # 任务名称（唯一，不可重复）
    task_name: str = db.Column(
        db.String(128), unique=True, nullable=False, index=True, comment="任务名称"
    )
    # 脚本文件路径（仅允许 .py 后缀）
    script_path: str = db.Column(
        db.String(512), nullable=False, comment="脚本路径（.py 文件）"
    )
    # cron 定时表达式（标准5位：分 时 日 月 周）
    cron_exp: str = db.Column(
        db.String(64), nullable=False, comment="cron 表达式（分 时 日 月 周）"
    )
    # 执行超时时间（秒，默认 3600）
    timeout: int = db.Column(
        db.Integer, default=3600, nullable=False, comment="执行超时时间（秒）"
    )
    # 是否启用
    is_active: bool = db.Column(
        db.Boolean, default=True, nullable=False, comment="是否启用"
    )
    # 最后一次执行状态（running/success/failed/timeout/stopped）
    last_status: Optional[str] = db.Column(
        db.String(20), default=None, nullable=True, comment="最后执行状态"
    )
    # 最后执行时间
    last_executed_at: Optional[datetime] = db.Column(
        db.DateTime(timezone=True), default=None, nullable=True, comment="最后执行时间"
    )
    # 当前运行中的进程 PID（用于强制终止）
    running_pid: Optional[int] = db.Column(
        db.Integer, default=None, nullable=True, comment="运行中的进程 PID"
    )
    # 执行参数（JSON格式）
    params: Optional[str] = db.Column(
        db.Text, default=None, nullable=True, comment="执行参数（JSON格式）"
    )
    # Python 执行路径（可配置）
    python_path: Optional[str] = db.Column(
        db.String(512), default=None, nullable=True, comment="Python执行路径"
    )

    # ====== 失败重试配置 ======
    # 最大重试次数（0 表示不重试）
    max_retries: int = db.Column(
        db.Integer, default=0, nullable=False, comment="最大重试次数（0=不重试）"
    )
    # 重试间隔基数（秒），实际间隔 = retry_delay * 2^当前重试次数（指数退避）
    retry_delay: int = db.Column(
        db.Integer, default=1, nullable=False, comment="重试间隔基数（秒，指数退避）"
    )

    # ====== 失败短信通知 ======
    # 是否开启短信通知（覆盖全局配置）
    sms_notify: Optional[bool] = db.Column(
        db.Boolean, default=None, nullable=True,
        comment="失败短信通知（None=跟随全局, True=开启, False=关闭）"
    )

    # 创建时间
    created_at: datetime = db.Column(
        db.DateTime(timezone=True), default=datetime.now, nullable=False,
        comment="创建时间"
    )
    # 更新时间（自动更新）
    updated_at: datetime = db.Column(
        db.DateTime(timezone=True), default=datetime.now, onupdate=datetime.now,
        nullable=False, comment="更新时间"
    )

    # 一对多关系：任务 -> 日志列表（级联删除）
    logs = db.relationship(
        "TaskLog",
        backref="task",
        lazy="dynamic",
        cascade="all, delete-orphan",
        order_by="TaskLog.start_time.desc()",
    )

    def to_dict(self) -> dict:
        """序列化为字典"""
        import json
        # 安全解析 JSON，防止损坏数据导致整个列表崩溃
        try:
            params = json.loads(self.params) if self.params else {}
        except (json.JSONDecodeError, TypeError):
            params = {}
        # 标签列表
        try:
            tag_list = [{"id": t.id, "name": t.name, "color": t.color} for t in self.tags]
        except Exception:
            tag_list = []
        return {
            "id": self.id,
            "task_name": self.task_name,
            "script_path": self.script_path,
            "cron_exp": self.cron_exp,
            "timeout": self.timeout,
            "is_active": self.is_active,
            "last_status": self.last_status,
            "last_executed_at": self.last_executed_at.strftime("%Y-%m-%d %H:%M:%S")
            if self.last_executed_at else None,
            "running_pid": self.running_pid,
            "params": params,
            "python_path": self.python_path,
            "max_retries": self.max_retries,
            "retry_delay": self.retry_delay,
            "sms_notify": self.sms_notify,
            "tags": tag_list,
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S")
            if self.created_at else None,
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M:%S")
            if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<ScriptTask id={self.id} name={self.task_name}>"
