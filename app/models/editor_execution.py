"""
文件编辑器执行记录模型
记录用户在文件编辑器中执行 Python 脚本的历史记录
"""
import os
from datetime import datetime
from typing import Optional

from app.extensions import db


class EditorExecutionLog(db.Model):
    """
    文件编辑器执行记录表
    每次在编辑器中执行脚本创建一条记录，保存一天后自动清理
    """

    __tablename__: str = "editor_execution_log"
    __allow_unmapped__ = True

    # 主键
    id: int = db.Column(db.Integer, primary_key=True, autoincrement=True)
    
    # 关联用户 ID（外键）
    user_id: int = db.Column(
        db.Integer,
        db.ForeignKey("sys_user.id", ondelete="CASCADE"),
        nullable=False,
        comment="执行用户 ID",
    )
    
    # 脚本文件路径
    script_path: str = db.Column(
        db.String(512), nullable=False, comment="脚本文件路径"
    )
    
    # Python 解释器路径
    python_path: str = db.Column(
        db.String(512), nullable=True, comment="Python 解释器路径"
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
    
    # 退出码
    return_code: Optional[int] = db.Column(
        db.Integer, default=None, nullable=True, comment="进程退出码"
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

    # 索引：加速查询和清理
    __table_args__ = (
        db.Index("idx_user_starttime", "user_id", "start_time"),
        db.Index("idx_status_starttime", "status", "start_time"),
    )

    def to_dict(self, include_log: bool = False) -> dict:
        """序列化为字典（默认不含日志内容，避免列表接口传输大量数据）"""
        result = {
            "id": self.id,
            "user_id": self.user_id,
            "username": "",
            "script_path": self.script_path,
            "script_name": self.script_path.split(os.sep)[-1] if self.script_path else "",
            "python_path": self.python_path,
            "status": self.status,
            "pid": self.pid,
            "return_code": self.return_code,
            "start_time": self.start_time.strftime("%Y-%m-%d %H:%M:%S")
            if self.start_time else None,
            "end_time": self.end_time.strftime("%Y-%m-%d %H:%M:%S")
            if self.end_time else None,
            "duration": self._get_duration(),
        }
        if include_log:
            result["log_content"] = self.log_content or ""
        return result

    def _get_duration(self) -> float:
        """计算执行耗时（秒）"""
        if self.start_time and self.end_time:
            return round((self.end_time - self.start_time).total_seconds(), 1)
        return 0.0

    def __repr__(self) -> str:
        return (
            f"<EditorExecutionLog id={self.id} user_id={self.user_id} "
            f"script={self.script_path} status={self.status}>"
        )