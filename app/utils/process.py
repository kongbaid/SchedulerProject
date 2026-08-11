"""
进程管理工具模块
提供跨平台的进程树终止功能，防止僵尸进程残留
"""
import logging

import psutil

logger = logging.getLogger(__name__)


def kill_process_tree(pid: int, timeout: float = 5.0) -> bool:
    """
    终止指定进程及其所有子进程（跨平台）
    使用 psutil 递归查找并杀死整个进程树

    :param pid:     主进程 PID
    :param timeout: 等待进程终止的超时时间（秒）
    :return: True=成功终止, False=进程不存在或终止失败
    """
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        logger.warning(f"[ProcessKill] 进程 PID={pid} 已不存在")
        return True
    except psutil.AccessDenied:
        logger.error(f"[ProcessKill] 无权访问进程 PID={pid}")
        return False

    # 收集所有子进程（递归）
    children: list = parent.children(recursive=True)

    # 先终止所有子进程
    for child in children:
        _terminate_process(child, timeout)

    # 再终止主进程
    _terminate_process(parent, timeout)

    logger.info(
        f"[ProcessKill] 进程树已终止：主进程 PID={pid}，子进程数={len(children)}"
    )
    return True


def _terminate_process(proc: psutil.Process, timeout: float = 5.0) -> None:
    """
    终止单个进程，先尝试优雅终止，再强制杀死
    :param proc:    psutil.Process 实例
    :param timeout: 等待超时
    """
    try:
        if not proc.is_running():
            return

        # 第一步：发送 SIGTERM / terminate()（优雅退出）
        proc.terminate()

        try:
            proc.wait(timeout=timeout)
        except psutil.TimeoutExpired:
            # 第二步：强制杀死
            logger.warning(
                f"[ProcessKill] PID={proc.pid} 优雅终止超时，强制 kill"
            )
            proc.kill()
            try:
                proc.wait(timeout=2)
            except psutil.TimeoutExpired:
                logger.error(
                    f"[ProcessKill] PID={proc.pid} 强制 kill 后仍未退出"
                )

    except psutil.NoSuchProcess:
        # 进程已退出，忽略
        pass
    except psutil.AccessDenied as e:
        logger.error(f"[ProcessKill] 无权终止进程 PID={proc.pid}: {e}")
    except Exception as e:
        logger.error(f"[ProcessKill] 终止进程 PID={proc.pid} 异常: {e}")


def is_process_running(pid: int) -> bool:
    """
    检查进程是否仍在运行
    :param pid: 进程 PID
    :return: True=运行中
    """
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
