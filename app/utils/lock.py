"""
文件锁模块
用于多进程部署时确保 APScheduler 只启动一次
跨平台支持 Windows（msvcrt）和 Linux（fcntl）
"""
import os
import sys
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class FileLock:
    """
    跨平台文件锁
    使用 flock / msvcrt 实现，确保 Gunicorn 多 worker 下只启动一次调度器
    """

    def __init__(self, lock_file_path: str) -> None:
        """
        :param lock_file_path: 锁文件绝对路径
        """
        self._lock_file_path: str = lock_file_path
        self._lock_file: Optional[object] = None
        self._is_locked: bool = False

    def acquire(self) -> bool:
        """
        尝试获取文件锁（非阻塞）
        :return: True=获取成功（当前进程为主调度进程），False=已被其他进程锁定
        """
        try:
            # 确保锁文件目录存在
            lock_dir: str = os.path.dirname(self._lock_file_path)
            if lock_dir and not os.path.exists(lock_dir):
                os.makedirs(lock_dir, exist_ok=True)

            self._lock_file = open(self._lock_file_path, "w")

            if sys.platform == "win32":
                # Windows 平台：使用 msvcrt.locking
                import msvcrt
                try:
                    msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                    self._is_locked = True
                    # 写入当前进程 PID 便于排查
                    self._lock_file.write(str(os.getpid()))
                    self._lock_file.flush()
                    logger.info(
                        f"[FileLock] Windows 文件锁获取成功，PID={os.getpid()}"
                    )
                    return True
                except (IOError, OSError) as e:
                    logger.info(
                        f"[FileLock] Windows 文件锁已被占用，当前进程跳过调度器启动: {e}"
                    )
                    self._lock_file.close()
                    self._lock_file = None
                    return False
            else:
                # Linux/macOS 平台：使用 fcntl.flock
                import fcntl
                try:
                    fcntl.flock(
                        self._lock_file.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB
                    )
                    self._is_locked = True
                    self._lock_file.write(str(os.getpid()))
                    self._lock_file.flush()
                    logger.info(
                        f"[FileLock] Unix 文件锁获取成功，PID={os.getpid()}"
                    )
                    return True
                except (IOError, OSError) as e:
                    logger.info(
                        f"[FileLock] Unix 文件锁已被占用，当前进程跳过调度器启动: {e}"
                    )
                    self._lock_file.close()
                    self._lock_file = None
                    return False

        except Exception as e:
            logger.error(f"[FileLock] 文件锁获取异常: {e}")
            return False

    def release(self) -> None:
        """释放文件锁"""
        if not self._is_locked or self._lock_file is None:
            return

        try:
            if sys.platform == "win32":
                import msvcrt
                try:
                    self._lock_file.seek(0)
                    msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
            else:
                import fcntl
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)

            self._lock_file.close()
            self._is_locked = False
            logger.info("[FileLock] 文件锁已释放")

            # 清理锁文件
            try:
                if os.path.exists(self._lock_file_path):
                    os.remove(self._lock_file_path)
            except Exception:
                pass

        except Exception as e:
            logger.error(f"[FileLock] 文件锁释放异常: {e}")

    def __del__(self) -> None:
        """析构时自动释放锁"""
        self.release()
