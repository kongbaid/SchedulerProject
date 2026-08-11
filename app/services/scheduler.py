"""
任务调度器服务
基于 APScheduler 实现 cron 定时调度
支持多进程安全（文件锁）、动态增删任务、启动时自动同步
"""
import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.utils.lock import FileLock

logger = logging.getLogger(__name__)


class TaskScheduler:
    """
    任务调度器单例
    封装 APScheduler，提供任务动态管理接口
    """

    def __init__(self) -> None:
        self._scheduler: Optional[BackgroundScheduler] = None
        self._file_lock: Optional[FileLock] = None
        self._is_master: bool = False  # 是否为主调度进程

    @property
    def is_running(self) -> bool:
        """调度器是否正在运行"""
        return self._scheduler is not None and self._scheduler.running

    @property
    def is_master(self) -> bool:
        """当前进程是否为主调度进程"""
        return self._is_master

    def init_scheduler(self, app) -> None:
        """
        初始化调度器（在 app factory 中调用）
        使用文件锁确保多进程下只启动一次

        :param app: Flask app 实例
        """
        # 尝试获取文件锁
        lock_path: str = app.config.get("LOCK_FILE_PATH", ".scheduler.lock")
        self._file_lock = FileLock(lock_path)

        if not self._file_lock.acquire():
            logger.warning(
                "[Scheduler] 文件锁获取失败，当前进程不启动调度器（多进程模式）"
            )
            self._is_master = False
            return

        self._is_master = True

        # 创建 APScheduler 后台调度器
        self._scheduler = BackgroundScheduler(
            timezone=app.config.get("TIMEZONE", "Asia/Shanghai"),
            # 使用内存存储（任务从数据库动态加载）
            job_defaults={
                "coalesce": True,        # 合并错过的执行
                "max_instances": 1,      # 每个任务最多1个实例
                "misfire_grace_time": 60,  # 错过执行的容忍时间（秒）
            },
        )

        # 启动调度器
        self._scheduler.start()
        logger.info("[Scheduler] APScheduler 调度器已启动")

        # 从数据库同步所有活跃任务
        self._sync_tasks_from_db(app)

        # 注册日志清理定时任务（每天凌晨 02:00）
        self._register_cleanup_job(app)

    def _sync_tasks_from_db(self, app) -> None:
        """
        从数据库加载所有活跃任务，注册到调度器
        :param app: Flask app 实例
        """
        with app.app_context():
            try:
                from app.models.task import ScriptTask
                active_tasks = ScriptTask.query.filter_by(is_active=True).all()

                for task in active_tasks:
                    self.add_job(task.id, task.cron_exp)

                logger.info(
                    f"[Scheduler] 从数据库同步了 {len(active_tasks)} 个活跃任务"
                )
            except Exception as e:
                logger.error(f"[Scheduler] 同步数据库任务失败: {e}")

    def _register_cleanup_job(self, app) -> None:
        """
        注册日志清理定时任务
        :param app: Flask app 实例
        """
        if not self._scheduler:
            return

        cleanup_hour: int = app.config.get("CLEANUP_HOUR", 2)
        cleanup_minute: int = app.config.get("CLEANUP_MINUTE", 0)

        self._scheduler.add_job(
            func=_run_cleanup,
            trigger=CronTrigger(hour=cleanup_hour, minute=cleanup_minute),
            id="system_log_cleanup",
            name="系统日志自动清理",
            replace_existing=True,
        )
        logger.info(
            f"[Scheduler] 日志清理任务已注册，每天 {cleanup_hour:02d}:{cleanup_minute:02d} 执行"
        )

    def add_job(self, task_id: int, cron_exp: str) -> bool:
        """
        添加定时任务到调度器
        :param task_id:  任务 ID
        :param cron_exp: cron 表达式（分 时 日 月 周）
        :return: True=成功
        """
        if not self._scheduler or not self._is_master:
            return False

        job_id: str = f"task_{task_id}"

        try:
            trigger = _parse_cron(cron_exp)
            self._scheduler.add_job(
                func=_execute_task_wrapper,
                trigger=trigger,
                id=job_id,
                name=f"task_job_{task_id}",
                args=[task_id],
                replace_existing=True,  # 如果已存在则替换
            )
            logger.info(
                f"[Scheduler] 添加定时任务 ID={task_id}，cron='{cron_exp}'"
            )
            return True
        except Exception as e:
            logger.error(
                f"[Scheduler] 添加定时任务 ID={task_id} 失败: {e}"
            )
            return False

    def remove_job(self, task_id: int) -> bool:
        """
        从调度器移除定时任务
        :param task_id: 任务 ID
        :return: True=成功
        """
        if not self._scheduler or not self._is_master:
            return False

        job_id: str = f"task_{task_id}"
        try:
            self._scheduler.remove_job(job_id)
            logger.info(f"[Scheduler] 移除定时任务 ID={task_id}")
            return True
        except Exception:
            # 任务可能不存在，忽略
            return True

    def update_job(self, task_id: int, cron_exp: str) -> bool:
        """
        更新定时任务的 cron 表达式
        :param task_id:  任务 ID
        :param cron_exp: 新的 cron 表达式
        :return: True=成功
        """
        # 先移除再添加（简单可靠）
        self.remove_job(task_id)
        return self.add_job(task_id, cron_exp)

    def get_next_run_time(self, task_id: int) -> Optional[str]:
        """
        获取任务的下一次运行时间
        :param task_id: 任务 ID
        :return: 格式化时间字符串或 None
        """
        if not self._scheduler or not self._is_master:
            return None

        job_id = f"task_{task_id}"
        try:
            job = self._scheduler.get_job(job_id)
            if job and job.next_run_time:
                return job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
        return None

    def shutdown(self) -> None:
        """关闭调度器，释放资源"""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("[Scheduler] 调度器已关闭")

        if self._file_lock:
            self._file_lock.release()
            self._is_master = False


def _parse_cron(cron_exp: str) -> CronTrigger:
    """
    解析标准5位 cron 表达式（分 时 日 月 周）
    转换为 APScheduler CronTrigger

    :param cron_exp: cron 表达式，如 "30 2 * * 0"
    :return: CronTrigger 实例
    """
    parts = cron_exp.strip().split()
    if len(parts) != 5:
        raise ValueError(
            f"cron 表达式格式错误，需要5位（分 时 日 月 周）: '{cron_exp}'"
        )

    minute, hour, day, month, day_of_week = parts

    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
    )


def _execute_task_wrapper(task_id: int) -> None:
    """
    调度器回调包装函数
    由 APScheduler 在定时触发时调用
    """
    from app.services.executor import execute_task
    execute_task(task_id, trigger_type="cron")


def _run_cleanup() -> None:
    """
    日志清理回调函数
    由 APScheduler 每天凌晨调用
    """
    from app.services.cleaner import cleanup_old_logs
    cleanup_old_logs()


# ============================================================
# 全局调度器单例
# ============================================================
scheduler = TaskScheduler()
