"""
任务执行服务
核心模块：负责脚本执行、防重复锁、日志记录、超时强杀
"""
import os
import sys
import json
import time
import logging
import subprocess
import threading
from datetime import datetime
from typing import Dict, Optional

# ============================================================
# 内存保护常量
# ============================================================
MAX_OUTPUT_LINES = 50000          # 最多收集 5 万行输出
MAX_OUTPUT_SIZE = 5 * 1024 * 1024  # 单任务最大输出 5MB
MAX_LOG_CONTENT = 200 * 1024       # 写入数据库的日志最大 200KB
MAX_POST_OUTPUT = 50 * 1024        # 传递给后处理函数的最大 50KB

from flask import current_app
from app.extensions import db
from app.models.task import ScriptTask, TaskDependency
from app.models.log import TaskLog
from app.utils.process import kill_process_tree, is_process_running

logger = logging.getLogger(__name__)

# ============================================================
# 全局内存锁：同一进程内防止同任务并发执行
# key = task_id, value = threading.Lock
# ============================================================
_task_locks: Dict[int, threading.Lock] = {}
_locks_mutex = threading.Lock()


def _get_task_lock(task_id: int) -> threading.Lock:
    """获取指定任务的线程锁（懒创建）"""
    with _locks_mutex:
        if task_id not in _task_locks:
            _task_locks[task_id] = threading.Lock()
        return _task_locks[task_id]


def _cleanup_task_lock(task_id: int) -> None:
    """清理已删除任务的锁"""
    with _locks_mutex:
        _task_locks.pop(task_id, None)


def execute_task(
    task_id: int,
    trigger_type: str = "manual",
    override_params: Optional[str] = None,
    retry_count: int = 0,
    _chain_visited: Optional[set] = None,
    _depth: int = 0,
) -> Optional[int]:
    """
    执行指定任务（核心入口）

    防重复执行策略（双重锁）：
    1. 内存锁：同一进程内 threading.Lock 互斥
    2. 数据库锁：检查 running_pid 是否对应存活进程

    :param task_id:         任务 ID
    :param trigger_type:    触发类型 manual/cron/dependency/retry
    :param override_params: 覆盖参数（JSON 字符串），用于手动执行时临时修改参数
    :param retry_count:     当前重试次数（0=首次执行）
    :param _chain_visited:  依赖链已触发任务 ID 集合（防循环）
    :param _depth:          依赖链深度（防循环）
    :return: TaskLog ID 或 None（重复触发时返回 None）
    """
    # ====== 第一重锁：内存锁（非阻塞尝试）======
    task_lock = _get_task_lock(task_id)
    acquired = task_lock.acquire(blocking=False)
    if not acquired:
        logger.warning(
            f"[Executor] 任务 ID={task_id} 已在当前进程执行中，跳过重复触发"
        )
        return None

    try:
        # 获取 Flask 应用实例（兼容调度器线程和请求上下文）
        from app import _app_instance
        app = _app_instance
        if app is None:
            try:
                app = current_app._get_current_object()
            except RuntimeError:
                logger.error("[Executor] 无法获取 Flask app 实例")
                return None
        with app.app_context():
            return _do_execute(task_id, trigger_type, override_params, retry_count, _chain_visited, _depth)
    except Exception as e:
        logger.error(f"[Executor] 任务 ID={task_id} 执行异常: {e}", exc_info=True)
        return None
    finally:
        task_lock.release()


def _do_execute(
    task_id: int,
    trigger_type: str,
    override_params: Optional[str] = None,
    retry_count: int = 0,
    _chain_visited: Optional[set] = None,
    _depth: int = 0,
) -> Optional[int]:
    """
    实际执行逻辑（在 app_context 内运行）
    :param task_id:         任务 ID
    :param trigger_type:    触发类型
    :param override_params: 覆盖参数（JSON 字符串）
    :param retry_count:     当前重试次数
    :param _chain_visited:  依赖链已触发任务 ID 集合（防循环）
    :param _depth:          依赖链深度（防循环）
    :return: TaskLog ID
    """
    # 查询任务
    task: Optional[ScriptTask] = db.session.get(ScriptTask, task_id)
    if not task:
        logger.error(f"[Executor] 任务 ID={task_id} 不存在")
        return None

    # ====== 第二重锁：数据库锁（检查是否有存活进程）======
    if task.running_pid and is_process_running(task.running_pid):
        logger.warning(
            f"[Executor] 任务 '{task.task_name}'(ID={task_id}) "
            f"已有运行中进程 PID={task.running_pid}，跳过重复触发"
        )
        return None

    # ====== 第三重锁：检查是否有 running 状态的日志（防止线程已启动但 PID 尚未写入的竞态）======
    running_log = TaskLog.query.filter_by(
        task_id=task_id, status="running"
    ).first()
    if running_log:
        # 检查该日志的进程是否真的还活着
        if running_log.pid and is_process_running(running_log.pid):
            logger.warning(
                f"[Executor] 任务 '{task.task_name}'(ID={task_id}) "
                f"有运行中的日志 ID={running_log.id} (PID={running_log.pid})，跳过重复触发"
            )
            return None
        # 进程已死但状态未更新，自动修复
        running_log.status = "stopped"
        running_log.end_time = datetime.now()
        running_log.log_content = (running_log.log_content or "") + "\n[SYSTEM] 进程已不存在，自动标记为已停止\n"
        running_log.pid = None
        db.session.commit()

    # 校验脚本路径安全性
    script_path: str = task.script_path
    if not _validate_script_path(script_path):
        logger.error(f"[Executor] 脚本路径校验失败: {script_path}")
        return None

    # 创建执行日志（状态=running）
    # 使用覆盖参数或默认参数
    exec_params = override_params if override_params is not None else task.params
    task_log = TaskLog(
        task_id=task_id,
        trigger_type=trigger_type,
        status="running",
        log_content="",
        exec_params=exec_params,  # 记录执行时的参数快照
        retry_count=retry_count,
        start_time=datetime.now(),
    )
    db.session.add(task_log)
    db.session.flush()  # 获取 log.id

    # 更新任务状态
    task.last_status = "running"
    task.last_executed_at = datetime.now()
    db.session.commit()

    log_id: int = task_log.id
    logger.info(
        f"[Executor] 开始执行任务 '{task.task_name}'，日志 ID={log_id}"
    )

    # ====== 在独立线程中执行脚本（避免阻塞调度器）======
    thread = threading.Thread(
        target=_run_in_thread,
        args=(task_id, log_id, script_path, task.timeout, exec_params, _chain_visited, _depth),
        daemon=True,
        name=f"task-{task_id}-log-{log_id}",
    )
    thread.start()

    return log_id


def _run_in_thread(
    task_id: int,
    log_id: int,
    script_path: str,
    timeout: int,
    exec_params: Optional[str] = None,
    _chain_visited: Optional[set] = None,
    _depth: int = 0,
) -> None:
    """
    在独立线程中运行脚本，执行完毕后更新数据库
    直接使用 Popen 以便立即获取并存储 PID
    
    :param exec_params: 执行参数（JSON 字符串），优先于 task.params
    :param _chain_visited: 依赖链已触发任务 ID 集合（防循环）
    :param _depth: 依赖链深度（防循环）
    """
    from app import _app_instance
    from app.routes.task import resolve_script_path

    if _app_instance is None:
        logger.error("[Executor] 无法获取 Flask app 实例")
        return

    app = _app_instance

    with app.app_context():
        process: Optional[subprocess.Popen] = None
        try:
            task_log: Optional[TaskLog] = db.session.get(TaskLog, log_id)
            task: Optional[ScriptTask] = db.session.get(ScriptTask, task_id)

            if not task_log or not task:
                logger.error(
                    f"[Executor] 日志或任务不存在: log_id={log_id}, task_id={task_id}"
                )
                return

            # 解析脚本路径（确保是相对于项目目录的绝对路径）
            script_path = resolve_script_path(script_path)

            # 构建子进程命令（-u 禁用缓冲，确保实时输出）
            python_exe: str = task.python_path or sys.executable
            
            # 验证 Python 解释器是否存在
            if not os.path.isfile(python_exe):
                error_msg = f"[Executor] Python 解释器不存在: {python_exe}"
                logger.error(error_msg)
                task_log.status = "failed"
                task_log.log_content = error_msg
                task_log.end_time = datetime.now()
                task.running_pid = None
                task.last_status = "failed"
                db.session.commit()
                return
            
            # 验证脚本文件是否存在
            if not os.path.isfile(script_path):
                error_msg = f"[Executor] 脚本文件不存在: {script_path}"
                logger.error(error_msg)
                task_log.status = "failed"
                task_log.log_content = error_msg
                task_log.end_time = datetime.now()
                task.running_pid = None
                task.last_status = "failed"
                db.session.commit()
                return
            
            cmd = [python_exe, "-u", script_path]
            
            # 解析 JSON 参数并添加到命令（优先使用 exec_params）
            params_to_use = exec_params or task.params
            if params_to_use:
                try:
                    params_dict = json.loads(params_to_use)
                    if isinstance(params_dict, dict):
                        # 将 JSON 转换为 argparse 参数格式：--key value
                        for key, value in params_dict.items():
                            if value is None:
                                # None 值跳过
                                continue
                            elif isinstance(value, bool):
                                # 布尔值：--key true/false（小写，方便 argparse 解析）
                                cmd.append(f"--{key}")
                                cmd.append(str(value).lower())
                            elif isinstance(value, list):
                                # 列表值：用逗号连接 --key v1,v2,v3
                                cmd.append(f"--{key}")
                                cmd.append(",".join(str(v) for v in value))
                            elif isinstance(value, dict):
                                # 嵌套对象：序列化为 JSON 字符串
                                cmd.append(f"--{key}")
                                cmd.append(json.dumps(value, ensure_ascii=False))
                            else:
                                # 字符串 / 数字
                                cmd.append(f"--{key}")
                                cmd.append(str(value))
                        logger.info(
                            f"[Executor] 任务 '{task.task_name}' 传递参数: {params_dict}"
                        )
                    else:
                        logger.warning(
                            f"[Executor] 任务 '{task.task_name}' 参数不是 JSON 对象，已忽略"
                        )
                except json.JSONDecodeError as e:
                    logger.error(
                        f"[Executor] 任务 '{task.task_name}' 参数解析失败: {e}"
                    )
            
            cwd = os.path.dirname(os.path.abspath(script_path))

            # 设置子进程环境变量，强制使用 UTF-8 编码
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'  # 强制子进程 stdout/stderr 使用 UTF-8
            env['PYTHONUTF8'] = '1'  # Python 3.7+ 启用 UTF-8 模式
            
            # 将项目根目录加入 PYTHONPATH，使脚本能导入项目根目录下的模块
            # 例如 spider_login/xxx.py 可以 import redis_util（位于项目根）
            app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
            existing_pp = env.get('PYTHONPATH', '')
            sep = os.pathsep
            env['PYTHONPATH'] = app_root + (sep + existing_pp if existing_pp else '')

            # 跨平台创建独立进程组
            kwargs: dict = dict(
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                text=True,
                encoding='utf-8',  # 显式指定 UTF-8 编码，避免 Windows GBK 解码错误
                errors='replace',  # 遇到无法解码的字符用 ? 替换
                bufsize=1,
                env=env,  # 传递环境变量
            )
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["preexec_fn"] = os.setsid

            process = subprocess.Popen(cmd, **kwargs)
            pid: int = process.pid

            # 立即存储 PID（用于手动停止）
            task.running_pid = pid
            task_log.pid = pid
            db.session.commit()
            logger.info(
                f"[Executor] 任务 '{task.task_name}' 子进程已启动，PID={pid}"
            )

            # ====== 独立超时监控线程（解决脚本无输出时超时检测失效问题）======
            timed_out: bool = False
            timeout_event = threading.Event()  # 用于通知监控线程退出
            start_ts = time.monotonic()

            def _timeout_monitor(proc_pid: int, timeout_sec: int, check_interval: float = 5.0):
                """独立监控线程：定时检查超时，不依赖输出循环"""
                nonlocal timed_out
                while not timeout_event.is_set():
                    elapsed = time.monotonic() - start_ts
                    if elapsed > timeout_sec:
                        timed_out = True
                        logger.warning(
                            f"[Executor] 任务 '{task.task_name}'(PID={proc_pid}) "
                            f"执行超过 {timeout_sec} 秒，超时监控线程触发强杀"
                        )
                        kill_process_tree(proc_pid)
                        return
                    # 等待 check_interval 秒或被 event 唤醒
                    timeout_event.wait(timeout=check_interval)

            monitor_thread = threading.Thread(
                target=_timeout_monitor,
                args=(pid, timeout),
                daemon=True,
                name=f"timeout-monitor-{pid}",
            )
            monitor_thread.start()

            # 实时读取输出（带内存保护）
            output_lines: list = []
            output_size: int = 0           # 已收集的字节数（估算）
            output_truncated: bool = False  # 是否已截断
            last_flush_time = start_ts      # 上次刷新日志的时间
            FLUSH_INTERVAL = 2              # 每2秒刷新一次日志到数据库
            last_flush_size = 0             # 上次刷新时已写入的字节数（增量刷新用）
            try:
                for line in process.stdout:
                    # 检查是否已被监控线程标记超时
                    if timed_out:
                        output_lines.append(
                            f"\n[TIMEOUT] 脚本执行超过 {timeout} 秒，已被超时监控线程强制终止\n"
                        )
                        break
                    # 辅助超时检测（快速响应）
                    if time.monotonic() - start_ts > timeout:
                        timed_out = True
                        output_lines.append(
                            f"\n[TIMEOUT] 脚本执行超过 {timeout} 秒，强制终止...\n"
                        )
                        kill_process_tree(pid)
                        break

                    # 内存保护：行数或字节数超限则截断
                    if not output_truncated:
                        line_bytes = len(line.encode('utf-8', errors='replace'))
                        if (len(output_lines) >= MAX_OUTPUT_LINES
                                or output_size + line_bytes > MAX_OUTPUT_SIZE):
                            output_truncated = True
                            output_lines.append(
                                f"\n[TRUNCATED] 输出已超过限制"
                                f"（{len(output_lines)}行/{output_size}字节），"
                                f"后续输出已丢弃\n"
                            )
                            # 不再继续收集，但仍需等待进程结束
                            break
                        output_lines.append(line)
                        output_size += line_bytes
                    
                    # 定期增量刷新日志到数据库（实时可见）
                    current_time = time.monotonic()
                    if current_time - last_flush_time >= FLUSH_INTERVAL:
                        try:
                            task_log_refresh = db.session.get(TaskLog, log_id)
                            if task_log_refresh:
                                # 增量拼接：只追加新增部分
                                new_content = "".join(output_lines)
                                if len(new_content) > last_flush_size:
                                    # 限制写入数据库的大小
                                    if len(new_content) > MAX_LOG_CONTENT:
                                        new_content = new_content[:MAX_LOG_CONTENT] + "\n...[已截断]"
                                    task_log_refresh.log_content = new_content
                                    last_flush_size = len(new_content)
                                    db.session.commit()
                                    last_flush_time = current_time
                        except Exception as flush_err:
                            logger.warning(f"[Executor] 刷新日志失败: {flush_err}")
                            db.session.rollback()
                
                # 读取完成后等待进程退出（如果未被超时终止）
                if not timed_out:
                    remaining = max(1, timeout - int(time.monotonic() - start_ts))
                    try:
                        process.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        output_lines.append(
                            f"\n[TIMEOUT] 脚本执行超过 {timeout} 秒，强制终止...\n"
                        )
                        kill_process_tree(pid)
                elif output_truncated:
                    # 截断后仍需等待进程结束（避免僵尸进程）
                    # 先排空 stdout 管道防止子进程阻塞
                    try:
                        process.stdout.read()
                    except Exception:
                        pass
                    remaining = max(1, timeout - int(time.monotonic() - start_ts))
                    try:
                        process.wait(timeout=min(remaining, 30))
                    except subprocess.TimeoutExpired:
                        kill_process_tree(pid)
            except Exception as e:
                output_lines.append(f"\n[ERROR] 读取输出异常: {e}\n")
                if process.poll() is None:
                    kill_process_tree(pid)
            finally:
                # 通知监控线程退出
                timeout_event.set()
                monitor_thread.join(timeout=3)

            # 关闭 stdout 管道，释放文件描述符
            try:
                process.stdout.close()
            except Exception:
                pass

            # 确保进程已结束
            try:
                process.wait(timeout=3)
            except Exception:
                kill_process_tree(pid)

            # 拼接输出文本并截断保护
            output_text: str = "".join(output_lines)
            del output_lines  # 立即释放列表内存
            if len(output_text) > MAX_LOG_CONTENT:
                db_output = output_text[:MAX_LOG_CONTENT] + "\n...[日志已截断，完整输出超过限制]"
            else:
                db_output = output_text
            return_code: Optional[int] = process.returncode

            # 确定最终状态
            if timed_out:
                final_status = "timeout"
            elif return_code == 0:
                final_status = "success"
            else:
                final_status = "failed"

            # 更新日志（使用截断后的内容写入数据库）
            task_log.status = final_status
            task_log.log_content = db_output
            task_log.end_time = datetime.now()
            task_log.pid = None

            # 更新任务状态
            task.running_pid = None
            task.last_status = final_status
            db.session.commit()

            logger.info(
                f"[Executor] 任务 '{task.task_name}' 执行完成，"
                f"状态={final_status}，退出码={return_code}"
            )

            # 传递给后处理函数的输出也截断（避免栈中大字符串长时间占用）
            post_output = output_text if len(output_text) <= MAX_POST_OUTPUT else output_text[-MAX_POST_OUTPUT:]

            # ====== 任务完成后处理：重试 / 依赖触发 / 短信通知 ======
            _post_execution_handler(task_id, log_id, final_status, post_output, _chain_visited, _depth)
            del output_text, db_output, post_output  # 显式释放大字符串

        except Exception as e:
            logger.error(
                f"[Executor] 任务执行线程异常: {e}", exc_info=True
            )
            # 确保子进程被清理
            if process and process.poll() is None:
                kill_process_tree(process.pid)
            # 关闭 stdout 管道
            if process:
                try:
                    process.stdout.close()
                except Exception:
                    pass
            # 尝试更新日志状态为 failed
            try:
                task_log = db.session.get(TaskLog, log_id)
                task = db.session.get(ScriptTask, task_id)
                if task_log:
                    task_log.status = "failed"
                    task_log.log_content = f"[ERROR] 执行异常: {e}"
                    task_log.end_time = datetime.now()
                    task_log.pid = None
                if task:
                    task.running_pid = None
                    task.last_status = "failed"
                db.session.commit()
                # 失败后处理（重试+短信）
                if task:
                    _post_execution_handler(task_id, log_id, "failed", str(e), _chain_visited, _depth)
            except Exception as inner_e:
                logger.error(f"[Executor] 更新失败日志异常: {inner_e}")
                db.session.rollback()


def _post_execution_handler(
    task_id: int,
    log_id: int,
    final_status: str,
    output_text: str,
    _chain_visited: Optional[set] = None,
    _depth: int = 0,
) -> None:
    """
    任务执行完成后处理：
    1. 失败重试（指数退避）
    2. 成功后触发下游依赖任务
    3. 失败后短信通知

    注意：此函数在 app_context 内调用
    """
    try:
        task = db.session.get(ScriptTask, task_id)
        if not task:
            return

        if final_status == "success":
            # ====== 成功：触发下游依赖任务（传递链式参数）======
            _trigger_downstream_tasks(
                task_id, task.task_name,
                _chain_visited=_chain_visited,
                _depth=_depth,
            )
        elif final_status in ("failed", "timeout"):
            # ====== 失败/超时：重试或短信通知 ======
            _handle_task_failure(task, log_id, final_status, output_text)

    except Exception as e:
        logger.error(f"[Executor] 后处理异常: {e}", exc_info=True)


def _handle_task_failure(
    task: "ScriptTask", log_id: int, final_status: str, output_text: str
) -> None:
    """
    处理任务失败：
    1. 检查是否需要重试（指数退避）
    2. 重试耗尽后发送短信通知
    """
    try:
        # 获取当前日志的重试次数
        task_log = db.session.get(TaskLog, log_id)
        current_retry = task_log.retry_count if task_log else 0

        # 检查是否还有重试次数
        if task.max_retries > 0 and current_retry < task.max_retries:
            # 指数退避延迟：retry_delay * 2^current_retry
            delay = task.retry_delay * (2 ** current_retry)
            next_retry = current_retry + 1
            logger.info(
                f"[Executor] 任务 '{task.task_name}' 将在 {delay}秒 后进行第 {next_retry} 次重试"
            )

            # 在延迟后异步重试
            def _retry_after_delay(tid, next_retry_val, task_name):
                time.sleep(delay)
                from app import _app_instance
                if _app_instance is None:
                    logger.error("[Executor] 重试时无法获取 Flask app")
                    return
                with _app_instance.app_context():
                    # 重新查询任务（确保数据最新）
                    t = db.session.get(ScriptTask, tid)
                    if not t or not t.is_active:
                        logger.info(f"[Executor] 任务 '{task_name}' 已停用，跳过重试")
                        return
                    logger.info(
                        f"[Executor] 开始第 {next_retry_val} 次重试: '{task_name}'"
                    )
                    log_id_new = execute_task(tid, trigger_type="retry", retry_count=next_retry_val)
                    if log_id_new is None:
                        logger.warning(
                            f"[Executor] 重试触发失败: '{task_name}'"
                        )

            retry_thread = threading.Thread(
                target=_retry_after_delay,
                args=(task.id, next_retry, task.task_name),
                daemon=True,
                name=f"retry-{task.id}-attempt-{next_retry}",
            )
            retry_thread.start()
            return  # 还在重试中，不发送短信

        # ====== 重试耗尽，发送短信通知 ======
        _send_failure_notification(task, current_retry, output_text)

    except Exception as e:
        logger.error(f"[Executor] 处理任务失败异常: {e}", exc_info=True)


def _send_failure_notification(
    task: "ScriptTask", retry_count: int, output_text: str
) -> None:
    """发送任务失败通知"""
    try:
        from app.services.sms_service import should_notify_task, send_failure_sms

        if should_notify_task(task):
            # 截取最后 200 字符作为错误信息
            error_msg = output_text[-200:] if output_text else "未知错误"
            retry_info = f"已重试{retry_count}次" if retry_count > 0 else ""
            send_failure_sms(task.task_name, error_msg, retry_info)
    except Exception as e:
        logger.error(f"[Executor] 发送失败通知异常: {e}")


def _trigger_downstream_tasks(
    upstream_task_id: int,
    upstream_task_name: str,
    _chain_visited: Optional[set] = None,
    _depth: int = 0,
) -> None:
    """
    触发上游任务成功后的所有下游依赖任务
    支持链式执行：A成功 -> 触发B -> B成功 -> 触发C

    运行时防循环保护：
    - _chain_visited: 当前链路已触发过的任务集合
    - _depth: 当前链式深度，超过 10 层自动截断
    """
    MAX_CHAIN_DEPTH = 10

    if _chain_visited is None:
        _chain_visited = set()

    if _depth >= MAX_CHAIN_DEPTH:
        logger.warning(
            f"[Executor] 依赖链超过 {MAX_CHAIN_DEPTH} 层，"
            f"停止继续触发（起始任务: '{upstream_task_name}'）"
        )
        return

    try:
        deps = TaskDependency.query.filter_by(
            upstream_task_id=upstream_task_id, is_active=True
        ).all()

        if not deps:
            return

        logger.info(
            f"[Executor] 任务 '{upstream_task_name}' 成功，"
            f"触发 {len(deps)} 个下游依赖任务"
        )

        downstream_ids = [dep.downstream_task_id for dep in deps]
        downstream_tasks_map = {
            t.id: t
            for t in ScriptTask.query.filter(ScriptTask.id.in_(downstream_ids)).all()
        } if downstream_ids else {}

        for dep in deps:
            downstream_task = downstream_tasks_map.get(dep.downstream_task_id)
            if not downstream_task:
                logger.warning(
                    f"[Executor] 下游任务 ID={dep.downstream_task_id} 不存在"
                )
                continue

            if not downstream_task.is_active:
                logger.info(
                    f"[Executor] 下游任务 '{downstream_task.task_name}' 已停用，跳过"
                )
                continue

            # 防循环：如果下游任务已经在当前链路中触发过，跳过
            if downstream_task.id in _chain_visited:
                logger.warning(
                    f"[Executor] 检测到循环依赖，"
                    f"'{downstream_task.task_name}' 已在当前链路中执行过，跳过"
                )
                continue

            # 在独立线程中触发下游任务（不阻塞当前线程）
            def _trigger(tid, tname, visited, depth):
                time.sleep(1)  # 稍微延迟，确保当前任务完全完成
                from app import _app_instance
                if _app_instance is None:
                    logger.error("[Executor] 触发下游任务时无法获取 Flask app")
                    return
                with _app_instance.app_context():
                    logger.info(
                        f"[Executor] 依赖触发: '{upstream_task_name}' -> '{tname}' "
                        f"(链深{depth + 1})"
                    )
                    execute_task(
                        tid,
                        trigger_type="dependency",
                        _chain_visited=visited | {tid},
                        _depth=depth + 1,
                    )

            trigger_thread = threading.Thread(
                target=_trigger,
                args=(
                    downstream_task.id,
                    downstream_task.task_name,
                    _chain_visited.copy(),
                    _depth,
                ),
                daemon=True,
                name=f"dep-trigger-{upstream_task_id}-{downstream_task.id}",
            )
            trigger_thread.start()

    except Exception as e:
        logger.error(
            f"[Executor] 触发下游任务异常: {e}", exc_info=True
        )


def stop_task(task_id: int) -> bool:
    """
    强制停止正在运行的任务
    :param task_id: 任务 ID
    :return: True=成功停止
    """
    try:
        task: Optional[ScriptTask] = db.session.get(ScriptTask, task_id)
        if not task:
            return False

        if not task.running_pid:
            # 检查是否有 running 状态的日志
            running_log = TaskLog.query.filter_by(
                task_id=task_id, status="running"
            ).first()
            if running_log:
                running_log.status = "stopped"
                running_log.end_time = datetime.now()
                running_log.log_content = (
                    (running_log.log_content or "")
                    + "\n[STOPPED] 手动停止（进程 PID 未记录）\n"
                )
                task.last_status = "stopped"
                db.session.commit()
            return True

        pid: int = task.running_pid

        # 终止进程树
        kill_process_tree(pid)

        # 更新运行中的日志
        running_log = TaskLog.query.filter_by(
            task_id=task_id, status="running"
        ).first()
        if running_log:
            running_log.status = "stopped"
            running_log.end_time = datetime.now()
            running_log.log_content = (
                (running_log.log_content or "")
                + f"\n[STOPPED] 手动强制终止，PID={pid}\n"
            )
            running_log.pid = None

        # 更新任务状态
        task.running_pid = None
        task.last_status = "stopped"
        db.session.commit()

        logger.info(
            f"[Executor] 任务 '{task.task_name}'(ID={task_id}) 已强制停止"
        )
        return True

    except Exception as e:
        logger.error(f"[Executor] 停止任务 ID={task_id} 异常: {e}")
        db.session.rollback()
        return False


def _validate_script_path(script_path: str) -> bool:
    """
    校验脚本路径安全性
    - 仅允许 .py 后缀
    - 禁止路径穿越攻击
    - 禁止危险路径
    :param script_path: 脚本路径
    :return: True=安全
    """
    if not script_path:
        return False

    # 规范化路径
    normalized = os.path.normpath(script_path)

    # 仅允许 .py 后缀
    if not normalized.lower().endswith(".py"):
        return False

    # 禁止路径穿越
    if ".." in normalized:
        return False

    # 禁止危险目录
    dangerous_dirs = ["/etc", "/bin", "/sbin", "/usr/bin", "/usr/sbin",
                      "C:\\Windows", "C:\\Program Files"]
    for d in dangerous_dirs:
        if normalized.lower().startswith(d.lower()):
            return False

    return True


def repair_stale_tasks() -> None:
    """
    启动时修复异常任务：
    - 将状态为 running 但进程已不存在的任务标记为 stopped
    - 清理僵尸进程记录
    - 清理孤立的内存锁（任务已从数据库删除但锁仍在内存中）
    """
    try:
        stale_logs = TaskLog.query.filter_by(status="running").all()
        repaired_count = 0

        # 收集数据库中所有存在的 task_id
        existing_task_ids = {t.id for t in ScriptTask.query.with_entities(ScriptTask.id).all()}

        stale_task_ids = {log.task_id for log in stale_logs if log.task_id is not None}
        tasks_map = {
            t.id: t
            for t in ScriptTask.query.filter(ScriptTask.id.in_(stale_task_ids)).all()
        } if stale_task_ids else {}

        for log_entry in stale_logs:
            task = tasks_map.get(log_entry.task_id)

            # 检查进程是否真的在运行
            pid_alive = False
            if log_entry.pid and is_process_running(log_entry.pid):
                pid_alive = True
            if task and task.running_pid and is_process_running(task.running_pid):
                pid_alive = True

            if not pid_alive:
                # 进程已不存在，标记为 stopped
                log_entry.status = "stopped"
                log_entry.end_time = datetime.now()
                log_entry.log_content = (
                    (log_entry.log_content or "")
                    + "\n[SYSTEM] 系统重启，自动标记为已停止\n"
                )
                log_entry.pid = None

                if task:
                    task.running_pid = None
                    task.last_status = "stopped"

                repaired_count += 1

        # 清理孤立的内存锁（任务已从数据库删除但锁仍在内存中）
        with _locks_mutex:
            orphaned_ids = [tid for tid in _task_locks if tid not in existing_task_ids]
            for tid in orphaned_ids:
                del _task_locks[tid]
            if orphaned_ids:
                logger.info(f"[Executor] 启动修复：已清理 {len(orphaned_ids)} 个孤立内存锁")

        db.session.commit()
        if repaired_count > 0:
            logger.info(f"[Executor] 启动修复：已修复 {repaired_count} 条异常日志")

    except Exception as e:
        logger.error(f"[Executor] 启动修复异常: {e}")
        db.session.rollback()