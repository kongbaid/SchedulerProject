"""
任务执行日志模型 TaskLog
每次任务执行创建一条独立日志，记录完整输出、状态、时间
"""
from datetime import datetime
from typing import Optional

from app.extensions import db


class TaskLog(db.Model):
    """
    任务执行日志表
    每次执行产生一条记录，与任务强绑定
    """

    __tablename__: str = "task_log"
    __allow_unmapped__ = True

    # 主键
    id: int = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # 关联任务 ID（外键，级联删除）
    task_id: int = db.Column(
        db.Integer,
        db.ForeignKey("script_task.id", ondelete="CASCADE"),
        nullable=False,
        comment="关联任务 ID",
    )
    # 触发类型：manual（手动触发）/ cron（定时触发）
    trigger_type: str = db.Column(
        db.String(20), nullable=False, default="manual",
        comment="触发类型（manual/cron）"
    )
    # 执行状态：running / success / failed / timeout / stopped
    status: str = db.Column(
        db.String(20), nullable=False, default="running",
        comment="执行状态"
    )
    # 完整输出日志（TEXT 长文本）
    log_content: Optional[str] = db.Column(
        db.Text, default="", nullable=True, comment="完整输出日志"
    )
    # 执行进程 PID（用于手动停止）
    pid: Optional[int] = db.Column(
        db.Integer, default=None, nullable=True, comment="执行进程 PID"
    )
    # 执行时使用的参数（JSON格式，记录快照）
    exec_params: Optional[str] = db.Column(
        db.Text, default=None, nullable=True, comment="执行参数快照（JSON）"
    )
    # 当前是第几次重试（0=首次执行，1=第一次重试...）
    retry_count: int = db.Column(
        db.Integer, default=0, nullable=False, comment="当前重试次数"
    )
    # 开始时间
    start_time: datetime = db.Column(
        db.DateTime(timezone=True), default=datetime.now, nullable=False,
        comment="开始时间"
    )
    # 结束时间
    end_time: Optional[datetime] = db.Column(
        db.DateTime(timezone=True), default=None, nullable=True,
        comment="结束时间"
    )

    # 联合索引：加速按任务 + 状态 + 结束时间的查询与清理
    __table_args__ = (
        db.Index("idx_task_status_endtime", "task_id", "status", "end_time"),
    )

    def to_dict(self) -> dict:
        """序列化为字典"""
        import json
        # 安全解析 JSON
        try:
            exec_params = json.loads(self.exec_params) if self.exec_params else {}
        except (json.JSONDecodeError, TypeError):
            exec_params = {}
        return {
            "id": self.id,
            "task_id": self.task_id,
            "trigger_type": self.trigger_type,
            "status": self.status,
            "log_content": self.log_content or "",
            "pid": self.pid,
            "exec_params": exec_params,
            "retry_count": self.retry_count,
            "start_time": self.start_time.strftime("%Y-%m-%d %H:%M:%S")
            if self.start_time else None,
            "end_time": self.end_time.strftime("%Y-%m-%d %H:%M:%S")
            if self.end_time else None,
            "task_name": self.task.task_name if self.task else "",
        }

    def __repr__(self) -> str:
        return (
            f"<TaskLog id={self.id} task_id={self.task_id} "
            f"status={self.status}>"
        )
