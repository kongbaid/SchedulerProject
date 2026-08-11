"""
日志自动清理服务
每天凌晨自动删除 N 天前的历史日志，防止数据库无限膨胀
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


def cleanup_old_logs(retention_days: Optional[int] = None) -> int:
    """
    清理过期的任务执行日志
    删除 retention_days 天前的所有已完成日志

    :param retention_days: 保留天数，默认从配置读取（7天）
    :return: 删除的日志条数
    """
    from app import _app_instance
    if _app_instance is None:
        logger.error("[Cleaner] 无法获取 Flask app 实例")
        return 0

    app = _app_instance

    with app.app_context():
        try:
            from app.extensions import db
            from app.models.log import TaskLog
            from app.models.editor_execution import EditorExecutionLog

            total_deleted = 0

            # 清理定时任务日志（保留7天）
            if retention_days is None:
                retention_days = app.config.get("LOG_RETENTION_DAYS", 7)

            cutoff_date = datetime.now() - timedelta(days=retention_days)
            deleted_count: int = TaskLog.query.filter(
                TaskLog.end_time < cutoff_date,
                TaskLog.status != "running",
            ).delete(synchronize_session=False)
            total_deleted += deleted_count

            logger.info(
                f"[Cleaner] 定时任务日志清理：删除 {deleted_count} 条 "
                f"（{retention_days}天前）"
            )

            # 清理编辑器执行日志（保留1天）
            editor_cutoff_date = datetime.now() - timedelta(days=1)
            editor_deleted_count: int = EditorExecutionLog.query.filter(
                EditorExecutionLog.start_time < editor_cutoff_date
            ).delete(synchronize_session=False)
            total_deleted += editor_deleted_count

            if editor_deleted_count > 0:
                logger.info(
                    f"[Cleaner] 编辑器执行日志清理：删除 {editor_deleted_count} 条 "
                    f"（1天前）"
                )

            db.session.commit()

            logger.info(
                f"[Cleaner] 日志清理完成：共删除 {total_deleted} 条"
            )
            return total_deleted

        except Exception as e:
            logger.error(f"[Cleaner] 日志清理异常: {e}", exc_info=True)
            try:
                db.session.rollback()
            except Exception:
                pass
            return 0