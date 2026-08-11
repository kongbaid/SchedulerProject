"""
任务管理路由
处理任务的增删改查、启用/停用、手动触发/停止
"""
import os
import re
import json
import logging
from typing import Any

from flask import Blueprint, render_template, request
from flask_login import login_required, current_user

from app.extensions import db
from app.models.task import ScriptTask, TaskTag, TaskTagMapping, TaskDependency
from app.models.user import ROLE_ADMIN
from app.models.python_path import PythonPath
from app.models.system_config import SystemConfig
from app.utils.response import success_response, error_response
from app.services.executor import execute_task, stop_task, _cleanup_task_lock
from app.services.scheduler import scheduler

logger = logging.getLogger(__name__)

task_bp = Blueprint(
    "task", __name__,
    template_folder="../templates/tasks",
)

# cron 表达式正则校验（5位，允许数字、*、/、-、,）
CRON_PATTERN = re.compile(
    r"^(\S+\s+){4}\S+$"
)


def clean_path(path: str) -> str:
    """
    清理路径字符串，移除所有不可见的 Unicode 控制字符
    
    :param path: 原始路径字符串
    :return: 清理后的路径
    """
    if not path:
        return path
    
    # 移除所有 Unicode 格式控制字符和不可见字符
    # 包括：U+2000-U+200F（标点空格、双向控制等）
    #      U+2028-U+202E（行分隔符、双向嵌入等）
    #      U+FEFF（零宽不换行空格/BOM）
    #      U+0000-U+001F（控制字符，保留制表符）
    cleaned = re.sub(r'[\u2000-\u200F\u2028-\u202E\uFEFF\u0000-\u0008\u000B-\u001F]', '', path)
    
    # 去除首尾空白
    cleaned = cleaned.strip()
    
    return cleaned


def resolve_script_path(script_path: str) -> str:
    """
    解析脚本路径，将相对路径转换为相对于项目根目录的绝对路径
    
    :param script_path: 原始脚本路径
    :return: 解析后的绝对路径
    """
    from config import BASE_DIR
    
    # 清理路径
    script_path = clean_path(script_path)
    
    # Windows 特殊处理：以 \ 开头的路径在 Windows 上被认为是绝对路径
    # 但实际上应该相对于项目目录
    if script_path.startswith('\\') or script_path.startswith('/'):
        # 去掉开头的斜杠，作为相对于项目目录的路径
        script_path = script_path.lstrip('\\/')
        absolute_path = os.path.join(BASE_DIR, script_path)
        return os.path.normpath(absolute_path)
    
    # 如果是其他形式的绝对路径（如 C:\...），直接返回
    if os.path.isabs(script_path):
        return os.path.normpath(script_path)
    
    # 将相对路径转换为相对于项目根目录的绝对路径
    absolute_path = os.path.join(BASE_DIR, script_path)
    return os.path.normpath(absolute_path)


def validate_script_path_for_creation(script_path: str) -> tuple:
    """
    验证脚本路径是否有效（不检查文件是否存在，用于创建任务时）
    
    :param script_path: 脚本路径
    :return: (是否有效, 规范化路径或错误消息)
    """
    if not script_path:
        return False, "脚本路径不能为空"
    
    # 仅允许 .py 后缀
    if not script_path.lower().endswith(".py"):
        return False, "仅允许 .py 后缀的脚本文件"
    
    # 路径安全校验：禁止路径穿越
    if ".." in script_path:
        return False, "脚本路径不允许包含 '..'"
    
    # 解析路径（转换为相对于项目目录的绝对路径）
    normalized_path = resolve_script_path(script_path)
    
    return True, normalized_path


# ============================================================
# 页面路由
# ============================================================

@task_bp.route("/")
@task_bp.route("/tasks")
@login_required
def task_list_page() -> str:
    """任务列表页面"""
    return render_template("tasks/list.html")


# ============================================================
# API 接口
# ============================================================

@task_bp.route("/api/script/check", methods=["POST"])
@login_required
def check_script_exists() -> Any:
    """
    检查脚本文件是否存在
    请求体 JSON：script_path
    """
    try:
        data = request.get_json(silent=True) or {}
        script_path = (data.get("script_path") or "").strip()
        
        if not script_path:
            return error_response("脚本路径不能为空", code=400)
        
        # 使用统一的校验函数
        is_valid, result = validate_script_path_for_creation(script_path)
        if not is_valid:
            return error_response(result, code=400)
        
        normalized_path = result
        
        # 检查是否存在
        exists = os.path.isfile(normalized_path)
        
        return success_response(
            "脚本文件" + ("存在" if exists else "不存在"),
            data={
                "exists": exists,
                "path": normalized_path
            }
        )
    
    except Exception as e:
        logger.error(f"[Task] 检查脚本文件异常: {e}", exc_info=True)
        return error_response("检查脚本文件失败", code=500)


@task_bp.route("/api/script/create", methods=["POST"])
@login_required
def create_script_file() -> Any:
    """
    创建脚本文件（带模板）
    请求体 JSON：script_path, task_name
    """
    try:
        data = request.get_json(silent=True) or {}
        script_path = (data.get("script_path") or "").strip()
        task_name = (data.get("task_name") or "").strip()
        
        if not script_path:
            return error_response("脚本路径不能为空", code=400)
        
        # 使用统一的校验函数
        is_valid, result = validate_script_path_for_creation(script_path)
        if not is_valid:
            return error_response(result, code=400)
        
        normalized_path = result
        
        # 如果文件已存在，返回提示
        if os.path.isfile(normalized_path):
            return error_response(f"脚本文件已存在: {normalized_path}", code=400)
        
        # 创建目录
        script_dir = os.path.dirname(normalized_path)
        if script_dir:
            os.makedirs(script_dir, exist_ok=True)
        
        # 从模板文件读取脚本模板，替换占位符
        from datetime import datetime
        template_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'task', 'script_template.py')
        with open(template_path, 'r', encoding='utf-8') as tf:
            default_script = tf.read()
        default_script = default_script.replace('__TASK_NAME__', task_name or '未命名任务')
        default_script = default_script.replace('__CREATE_TIME__', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        
        with open(normalized_path, 'w', encoding='utf-8') as f:
            f.write(default_script)
        
        logger.info(f"[Task] 创建脚本文件: {normalized_path}")
        return success_response(
            "脚本文件创建成功",
            data={"path": normalized_path}
        )
    
    except Exception as e:
        logger.error(f"[Task] 创建脚本文件异常: {e}", exc_info=True)
        return error_response(f"创建脚本文件失败: {str(e)}", code=500)


@task_bp.route("/api/tasks")
@login_required
def task_list_api() -> Any:
    """
    获取任务列表（分页 + 搜索）
    GET 参数：page, per_page, keyword
    """
    try:
        page: int = request.args.get("page", 1, type=int)
        per_page: int = request.args.get("per_page", 10, type=int)
        keyword: str = request.args.get("keyword", "", type=str).strip()
        tag_id: int = request.args.get("tag_id", 0, type=int)

        # 参数安全边界
        page = max(1, page)
        per_page = min(max(1, per_page), 100)

        query = ScriptTask.query
        
        # 普通用户只能看到分配的任务
        if current_user.role != ROLE_ADMIN:
            query = query.filter(ScriptTask.managers.any(id=current_user.id))
        
        if keyword:
            query = query.filter(
                ScriptTask.task_name.like(f"%{keyword}%")
            )

        # 按标签筛选
        if tag_id:
            query = query.filter(ScriptTask.tags.any(id=tag_id))

        query = query.order_by(ScriptTask.created_at.desc())
        pagination = query.paginate(
            page=page, per_page=per_page, error_out=False
        )

        items = []
        for t in pagination.items:
            d = t.to_dict()
            # 添加下次运行时间（从调度器获取）
            d["next_run_time"] = scheduler.get_next_run_time(t.id) if t.is_active else None
            items.append(d)

        return success_response(data={
            "items": items,
            "total": pagination.total,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "pages": pagination.pages,
        })

    except Exception as e:
        logger.error(f"[Task] 获取任务列表异常: {e}", exc_info=True)
        return error_response("获取任务列表失败", code=500)


@task_bp.route("/api/task", methods=["POST"])
@login_required
def add_task() -> Any:
    """
    添加任务
    请求体 JSON：task_name, script_path, cron_exp, timeout, params, python_path
    """
    try:
        # 权限检查：普通用户需要有创建任务的权限
        if not current_user.has_create_task_permission():
            return error_response("没有权限创建任务，请联系管理员", code=403)
        
        data = request.get_json(silent=True) or {}
        task_name: str = (data.get("task_name") or "").strip()
        script_path: str = (data.get("script_path") or "").strip()
        cron_exp: str = (data.get("cron_exp") or "").strip()
        timeout: int = data.get("timeout", 3600)
        params: dict = data.get("params", {})
        python_path: str = (data.get("python_path") or "").strip()
        max_retries: int = data.get("max_retries", 0)
        retry_delay: int = data.get("retry_delay", 1)
        sms_notify = data.get("sms_notify")  # None/True/False
        tag_ids: list = data.get("tag_ids", [])  # 标签ID列表
        
        # ====== 清理和校验参数 ======
        # 清理路径中的不可见 Unicode 字符
        script_path = clean_path(script_path)
        python_path = clean_path(python_path)

        # ====== 参数校验 ======
        if not task_name:
            return error_response("任务名称不能为空", code=400)
        if len(task_name) > 128:
            return error_response("任务名称过长（最多128字符）", code=400)

        # 校验脚本路径格式（不检查是否存在）
        is_valid, result = validate_script_path_for_creation(script_path)
        if not is_valid:
            return error_response(result, code=400)
        script_path = result

        if not cron_exp:
            return error_response("cron 表达式不能为空", code=400)
        if not CRON_PATTERN.match(cron_exp):
            return error_response(
                "cron 表达式格式错误，需要5位（分 时 日 月 周）", code=400
            )

        if not isinstance(timeout, int) or timeout < 10 or timeout > 86400:
            return error_response("超时时间需在 10~86400 秒之间", code=400)

        # 校验 params（必须是 dict）
        if params and not isinstance(params, dict):
            return error_response("参数格式错误，必须为 JSON 对象", code=400)
        
        # 校验 python_path（如果提供）
        if python_path and not os.path.isfile(python_path):
            return error_response(f"Python 执行路径不存在: {python_path}", code=400)

        # 校验重试参数
        if not isinstance(max_retries, int) or max_retries < 0 or max_retries > 10:
            return error_response("重试次数需在 0~10 之间", code=400)
        if not isinstance(retry_delay, int) or retry_delay < 1 or retry_delay > 300:
            return error_response("重试间隔需在 1~300 秒之间", code=400)

        # ====== 唯一性校验 ======
        existing = ScriptTask.query.filter_by(task_name=task_name).first()
        if existing:
            return error_response(f"任务名称 '{task_name}' 已存在", code=400)

        # ====== 检查脚本文件是否存在 ======
        if not os.path.isfile(script_path):
            return error_response(
                f"脚本文件不存在: {script_path}",
                code=400,
                data={"script_not_exists": True, "script_path": script_path}
            )

        # ====== 创建任务 ======
        task = ScriptTask(
            task_name=task_name,
            script_path=script_path,
            cron_exp=cron_exp,
            timeout=timeout,
            is_active=True,
            params=json.dumps(params, ensure_ascii=False) if params else None,
            python_path=python_path or None,
            max_retries=max_retries,
            retry_delay=retry_delay,
            sms_notify=sms_notify if sms_notify is not None else None,
        )
        db.session.add(task)
        db.session.flush()  # 获取 task.id

        # 设置标签
        if tag_ids:
            _set_task_tags(task, tag_ids)

        db.session.commit()

        # 注册到调度器
        scheduler.add_job(task.id, cron_exp)

        logger.info(f"[Task] 添加任务成功: '{task_name}' (ID={task.id})")
        return success_response(
            "任务添加成功",
            data={"task": task.to_dict()}
        )

    except Exception as e:
        logger.error(f"[Task] 添加任务异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("添加任务失败", code=500)


@task_bp.route("/api/task/<int:task_id>", methods=["PUT"])
@login_required
def update_task(task_id: int) -> Any:
    """
    更新任务
    请求体 JSON：task_name, script_path, cron_exp, timeout, params, python_path
    """
    try:
        task = db.session.get(ScriptTask, task_id)
        if not task:
            return error_response("任务不存在", code=400)

        data = request.get_json(silent=True) or {}
        task_name: str = (data.get("task_name") or "").strip()
        script_path: str = (data.get("script_path") or "").strip()
        cron_exp: str = (data.get("cron_exp") or "").strip()
        timeout: int = data.get("timeout", task.timeout)
        # params: 前端可能传 dict，也可能未传（此时保留数据库原值）
        raw_params = data.get("params")
        python_path: str = data.get("python_path", task.python_path)
        max_retries = data.get("max_retries", task.max_retries)
        retry_delay = data.get("retry_delay", task.retry_delay)
        sms_notify = data.get("sms_notify") if "sms_notify" in data else task.sms_notify
        tag_ids = data.get("tag_ids")  # None 表示未传，不修改标签
        
        # 清理路径中的不可见 Unicode 字符
        if script_path:
            script_path = clean_path(script_path)
        if python_path:
            python_path = clean_path(python_path)

        # 参数校验
        if not task_name:
            return error_response("任务名称不能为空", code=400)
        if task_name != task.task_name:
            existing = ScriptTask.query.filter_by(task_name=task_name).first()
            if existing:
                return error_response(f"任务名称 '{task_name}' 已存在", code=400)

        # 校验脚本路径格式（不检查是否存在）
        if script_path:
            is_valid, result = validate_script_path_for_creation(script_path)
            if not is_valid:
                return error_response(result, code=400)
            script_path = result
        else:
            script_path = task.script_path

        if cron_exp:
            if not CRON_PATTERN.match(cron_exp):
                return error_response(
                    "cron 表达式格式错误，需要5位（分 时 日 月 周）", code=400
                )
        else:
            cron_exp = task.cron_exp

        if not isinstance(timeout, int) or timeout < 10 or timeout > 86400:
            return error_response("超时时间需在 10~86400 秒之间", code=400)

        # 校验 params
        if raw_params is not None:
            if not isinstance(raw_params, dict):
                return error_response("参数格式错误，必须为 JSON 对象", code=400)
            task.params = json.dumps(raw_params, ensure_ascii=False) if raw_params else None
        # 若未传 params，保留数据库原值不变
        
        # 校验重试参数
        if not isinstance(max_retries, int) or max_retries < 0 or max_retries > 10:
            return error_response("重试次数需在 0~10 之间", code=400)
        if not isinstance(retry_delay, int) or retry_delay < 1 or retry_delay > 300:
            return error_response("重试间隔需在 1~300 秒之间", code=400)

        # 校验 python_path
        if python_path and not os.path.isfile(python_path):
            return error_response(f"Python 执行路径不存在: {python_path}", code=400)

        # 更新字段
        task.task_name = task_name
        task.script_path = script_path
        task.cron_exp = cron_exp
        task.timeout = timeout
        task.python_path = python_path or None
        task.max_retries = max_retries
        task.retry_delay = retry_delay
        task.sms_notify = sms_notify if sms_notify is not None else None

        # 更新标签（在 commit 之前操作，确保标签映射一起提交）
        if tag_ids is not None:
            _set_task_tags(task, tag_ids)

        # 提交所有变更（任务字段 + 标签映射）
        db.session.commit()

        # 更新调度器
        if task.is_active:
            scheduler.update_job(task.id, cron_exp)

        logger.info(f"[Task] 更新任务成功: '{task_name}' (ID={task_id})")
        return success_response("任务更新成功", data={"task": task.to_dict()})

    except Exception as e:
        logger.error(f"[Task] 更新任务异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("更新任务失败", code=500)


@task_bp.route("/api/task/<int:task_id>")
@login_required
def task_detail_api(task_id: int) -> Any:
    """
    获取单个任务详情
    """
    try:
        task = db.session.get(ScriptTask, task_id)
        if not task:
            return error_response("任务不存在", code=404)
        
        # 普通用户只能查看分配给自己的任务
        if current_user.role != ROLE_ADMIN:
            if not task.managers.any(id=current_user.id):
                return error_response("没有权限查看此任务", code=403)
        
        d = task.to_dict()
        d["next_run_time"] = scheduler.get_next_run_time(task.id) if task.is_active else None
        return success_response(data=d)
    
    except Exception as e:
        logger.error(f"[Task] 获取任务详情异常: {e}", exc_info=True)
        return error_response("获取任务详情失败", code=500)


@task_bp.route("/api/task/<int:task_id>", methods=["DELETE"])
@login_required
def delete_task(task_id: int) -> Any:
    """删除任务（级联删除日志）"""
    try:
        task = db.session.get(ScriptTask, task_id)
        if not task:
            return error_response("任务不存在", code=400)

        # 如果正在运行，先停止
        if task.running_pid:
            stop_task(task_id)

        # 清理内存中的任务锁
        _cleanup_task_lock(task_id)

        # 从调度器移除
        scheduler.remove_job(task_id)

        task_name = task.task_name
        db.session.delete(task)
        db.session.commit()

        logger.info(f"[Task] 删除任务成功: '{task_name}' (ID={task_id})")
        return success_response("任务已删除")

    except Exception as e:
        logger.error(f"[Task] 删除任务异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("删除任务失败", code=500)


@task_bp.route("/api/task/<int:task_id>/toggle", methods=["POST"])
@login_required
def toggle_task(task_id: int) -> Any:
    """启用/停用任务"""
    try:
        task = db.session.get(ScriptTask, task_id)
        if not task:
            return error_response("任务不存在", code=400)

        task.is_active = not task.is_active
        db.session.commit()

        if task.is_active:
            scheduler.add_job(task.id, task.cron_exp)
            msg = f"任务 '{task.task_name}' 已启用"
        else:
            scheduler.remove_job(task.id)
            msg = f"任务 '{task.task_name}' 已停用"

        logger.info(f"[Task] {msg}")
        return success_response(msg, data={"task": task.to_dict()})

    except Exception as e:
        logger.error(f"[Task] 切换任务状态异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("操作失败", code=500)


@task_bp.route("/api/task/<int:task_id>/trigger", methods=["POST"])
@login_required
def trigger_task(task_id: int) -> Any:
    """手动触发任务执行（支持临时参数）"""
    try:
        task = db.session.get(ScriptTask, task_id)
        if not task:
            return error_response("任务不存在", code=400)

        # 解析并检查脚本是否存在
        script_path = resolve_script_path(task.script_path)
        if not os.path.isfile(script_path):
            return error_response(
                f"脚本文件不存在: {script_path}", code=400
            )

        # 支持传入临时参数（覆盖默认参数）
        override_params = None
        data = request.get_json(silent=True) or {}
        if "params" in data:
            params = data["params"]
            if params and isinstance(params, dict):
                override_params = json.dumps(params, ensure_ascii=False)
            elif not params:
                override_params = ""  # 空参数

        log_id = execute_task(task_id, trigger_type="manual", override_params=override_params)
        if log_id is None:
            return error_response(
                "任务正在运行中，不可重复触发", code=400
            )

        return success_response("任务已触发执行", data={"log_id": log_id})

    except Exception as e:
        logger.error(f"[Task] 触发任务异常: {e}", exc_info=True)
        return error_response("触发任务失败", code=500)


@task_bp.route("/api/task/<int:task_id>/stop", methods=["POST"])
@login_required
def stop_task_api(task_id: int) -> Any:
    """强制停止运行中的任务"""
    try:
        result = stop_task(task_id)
        if result:
            return success_response("任务已停止")
        return error_response("停止任务失败", code=500)

    except Exception as e:
        logger.error(f"[Task] 停止任务异常: {e}", exc_info=True)
        return error_response("停止任务失败", code=500)


# ============================================================
# Python 路径管理 API
# ============================================================

@task_bp.route("/api/python-paths", methods=["GET"])
@login_required
def list_python_paths() -> Any:
    """获取所有 Python 路径"""
    try:
        paths = PythonPath.query.order_by(PythonPath.is_default.desc(), PythonPath.id.asc()).all()
        return success_response(data=[p.to_dict() for p in paths])
    except Exception as e:
        logger.error(f"[PythonPath] 获取路径列表异常: {e}", exc_info=True)
        return error_response("获取 Python 路径失败", code=500)


@task_bp.route("/api/python-paths", methods=["POST"])
@login_required
def add_python_path() -> Any:
    """
    添加 Python 路径
    请求体 JSON：path, is_default, description
    """
    try:
        data = request.get_json(silent=True) or {}
        path = (data.get("path") or "").strip()
        is_default = bool(data.get("is_default", False))
        description = (data.get("description") or "").strip() or None
        
        if not path:
            return error_response("Python 路径不能为空", code=400)
        
        # 清理路径
        path = clean_path(path)
        
        # 检查是否已存在
        existing = PythonPath.query.filter_by(path=path).first()
        if existing:
            return error_response(f"该路径已存在: {path}", code=400)
        
        # 如果设为默认，先取消其他默认
        if is_default:
            PythonPath.query.update({"is_default": False})
        
        new_path = PythonPath(
            path=path,
            is_default=is_default,
            description=description
        )
        # 如果是第一条记录，自动设为默认（必须在 add 之前检查，否则 count 会包含刚添加的记录）
        if PythonPath.query.count() == 0:
            new_path.is_default = True
        
        db.session.add(new_path)
        db.session.commit()
        
        logger.info(f"[PythonPath] 添加路径: {path}")
        return success_response("Python 路径添加成功", data=new_path.to_dict())
    
    except Exception as e:
        logger.error(f"[PythonPath] 添加路径异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("添加 Python 路径失败", code=500)


@task_bp.route("/api/python-paths/<int:path_id>", methods=["PUT"])
@login_required
def update_python_path(path_id: int) -> Any:
    """
    更新 Python 路径（设为默认等）
    请求体 JSON：is_default, description
    """
    try:
        path_obj = db.session.get(PythonPath, path_id)
        if not path_obj:
            return error_response("路径不存在", code=400)
        
        data = request.get_json(silent=True) or {}
        
        if "is_default" in data and data["is_default"]:
            # 取消其他默认
            PythonPath.query.update({"is_default": False})
            path_obj.is_default = True
        
        if "description" in data:
            path_obj.description = data["description"] or None
        
        db.session.commit()
        
        logger.info(f"[PythonPath] 更新路径 ID={path_id}")
        return success_response("更新成功", data=path_obj.to_dict())
    
    except Exception as e:
        logger.error(f"[PythonPath] 更新路径异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("更新失败", code=500)


@task_bp.route("/api/python-paths/<int:path_id>", methods=["DELETE"])
@login_required
def delete_python_path(path_id: int) -> Any:
    """删除 Python 路径"""
    try:
        path_obj = db.session.get(PythonPath, path_id)
        if not path_obj:
            return error_response("路径不存在", code=400)
        
        was_default = path_obj.is_default
        path_info = path_obj.path  # 先保存信息用于日志
        db.session.delete(path_obj)
        db.session.flush()  # 立即从数据库中移除，确保下面的 query 不包含已删记录
        
        # 如果删除的是默认路径，将第一条设为默认
        if was_default:
            first = PythonPath.query.first()
            if first:
                first.is_default = True
        
        db.session.commit()
        
        logger.info(f"[PythonPath] 删除路径 ID={path_id}: {path_info}")
        return success_response("删除成功")
    
    except Exception as e:
        logger.error(f"[PythonPath] 删除路径异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("删除失败", code=500)


# ============================================================
# 标签管理 API
# ============================================================

def _set_task_tags(task: ScriptTask, tag_ids: list) -> None:
    """设置任务的标签（先清除再重新设置）"""
    # 清除旧标签
    TaskTagMapping.query.filter_by(task_id=task.id).delete()
    # 设置新标签
    valid_tag_ids = set()
    if tag_ids:
        valid_tag_ids = {
            t.id for t in TaskTag.query.filter(TaskTag.id.in_(tag_ids)).all()
        }
    for tid in tag_ids:
        if tid in valid_tag_ids:
            mapping = TaskTagMapping(task_id=task.id, tag_id=tid)
            db.session.add(mapping)


@task_bp.route("/api/tags", methods=["GET"])
@login_required
def list_tags() -> Any:
    """获取所有标签"""
    try:
        tags = TaskTag.query.order_by(TaskTag.id.asc()).all()
        return success_response(data=[t.to_dict() for t in tags])
    except Exception as e:
        logger.error(f"[Tag] 获取标签列表异常: {e}", exc_info=True)
        return error_response("获取标签失败", code=500)


@task_bp.route("/api/tags", methods=["POST"])
@login_required
def add_tag() -> Any:
    """
    创建标签
    请求体 JSON：name, color
    """
    try:
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        color = (data.get("color") or "#6c757d").strip()

        if not name:
            return error_response("标签名称不能为空", code=400)
        if len(name) > 64:
            return error_response("标签名称过长（最多64字符）", code=400)

        existing = TaskTag.query.filter_by(name=name).first()
        if existing:
            return error_response(f"标签 '{name}' 已存在", code=400)

        tag = TaskTag(name=name, color=color)
        db.session.add(tag)
        db.session.commit()

        logger.info(f"[Tag] 创建标签: '{name}'")
        return success_response("标签创建成功", data=tag.to_dict())

    except Exception as e:
        logger.error(f"[Tag] 创建标签异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("创建标签失败", code=500)


@task_bp.route("/api/tags/<int:tag_id>", methods=["PUT"])
@login_required
def update_tag(tag_id: int) -> Any:
    """更新标签"""
    try:
        tag = db.session.get(TaskTag, tag_id)
        if not tag:
            return error_response("标签不存在", code=400)

        data = request.get_json(silent=True) or {}
        if "name" in data:
            name = (data["name"] or "").strip()
            if not name:
                return error_response("标签名称不能为空", code=400)
            existing = TaskTag.query.filter(TaskTag.name == name, TaskTag.id != tag_id).first()
            if existing:
                return error_response(f"标签 '{name}' 已存在", code=400)
            tag.name = name
        if "color" in data:
            tag.color = data["color"]

        db.session.commit()
        return success_response("标签更新成功", data=tag.to_dict())

    except Exception as e:
        logger.error(f"[Tag] 更新标签异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("更新标签失败", code=500)


@task_bp.route("/api/tags/<int:tag_id>", methods=["DELETE"])
@login_required
def delete_tag(tag_id: int) -> Any:
    """删除标签"""
    try:
        tag = db.session.get(TaskTag, tag_id)
        if not tag:
            return error_response("标签不存在", code=400)

        db.session.delete(tag)
        db.session.commit()
        logger.info(f"[Tag] 删除标签: '{tag.name}'")
        return success_response("标签已删除")

    except Exception as e:
        logger.error(f"[Tag] 删除标签异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("删除标签失败", code=500)


# ============================================================
# 按标签批量启用/停用
# ============================================================

@task_bp.route("/api/tasks/batch-toggle", methods=["POST"])
@login_required
def batch_toggle_tasks() -> Any:
    """
    按标签批量启用/停用任务
    请求体 JSON：tag_id, action("enable"/"disable")
    """
    try:
        data = request.get_json(silent=True) or {}
        tag_id = data.get("tag_id")
        action = data.get("action")

        if not tag_id or action not in ("enable", "disable"):
            return error_response("参数错误：需要 tag_id 和 action(enable/disable)", code=400)

        tag = db.session.get(TaskTag, tag_id)
        if not tag:
            return error_response("标签不存在", code=400)

        is_active = action == "enable"
        tasks = tag.tasks.all()
        count = 0
        for t in tasks:
            if t.is_active != is_active:
                t.is_active = is_active
                if is_active:
                    scheduler.add_job(t.id, t.cron_exp)
                else:
                    scheduler.remove_job(t.id)
                count += 1

        db.session.commit()
        action_text = '启用' if is_active else '停用'
        msg = f"已{action_text} {count} 个任务"
        logger.info(f"[Task] 批量{msg}: 标签='{tag.name}'")
        return success_response(msg)

    except Exception as e:
        logger.error(f"[Task] 批量切换状态异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("操作失败", code=500)


# ============================================================
# 任务依赖关系 API
# ============================================================

@task_bp.route("/api/task/<int:task_id>/dependencies", methods=["GET"])
@login_required
def get_task_dependencies(task_id: int) -> Any:
    """
    获取任务的依赖关系
    返回：upstream(谁在我之前) + downstream(我之后是谁)
    """
    try:
        task = db.session.get(ScriptTask, task_id)
        if not task:
            return error_response("任务不存在", code=400)

        # 上游：谁完成后触发我
        upstream = TaskDependency.query.filter_by(
            downstream_task_id=task_id
        ).all()
        # 下游：我完成后触发谁
        downstream = TaskDependency.query.filter_by(
            upstream_task_id=task_id
        ).all()

        return success_response(data={
            "upstream": [d.to_dict() for d in upstream],
            "downstream": [d.to_dict() for d in downstream],
        })

    except Exception as e:
        logger.error(f"[Dep] 获取依赖关系异常: {e}", exc_info=True)
        return error_response("获取依赖关系失败", code=500)


@task_bp.route("/api/task/dependencies", methods=["POST"])
@login_required
def add_dependency() -> Any:
    """
    添加任务依赖关系
    请求体 JSON：upstream_task_id, downstream_task_id
    """
    try:
        data = request.get_json(silent=True) or {}
        upstream_id = data.get("upstream_task_id")
        downstream_id = data.get("downstream_task_id")

        if not upstream_id or not downstream_id:
            return error_response("需要 upstream_task_id 和 downstream_task_id", code=400)

        if upstream_id == downstream_id:
            return error_response("任务不能依赖自己", code=400)

        # 检查任务是否存在
        up = db.session.get(ScriptTask, upstream_id)
        down = db.session.get(ScriptTask, downstream_id)
        if not up or not down:
            return error_response("上游或下游任务不存在", code=400)

        # 检查循环依赖（简单检测）
        if _has_circular_dependency(upstream_id, downstream_id):
            return error_response("检测到循环依赖，无法添加", code=400)

        # 检查是否已存在
        existing = TaskDependency.query.filter_by(
            upstream_task_id=upstream_id, downstream_task_id=downstream_id
        ).first()
        if existing:
            return error_response("该依赖关系已存在", code=400)

        dep = TaskDependency(
            upstream_task_id=upstream_id,
            downstream_task_id=downstream_id,
        )
        db.session.add(dep)
        db.session.commit()

        logger.info(
            f"[Dep] 添加依赖: '{up.task_name}' -> '{down.task_name}'"
        )
        return success_response("依赖关系添加成功", data=dep.to_dict())

    except Exception as e:
        logger.error(f"[Dep] 添加依赖异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("添加依赖失败", code=500)


@task_bp.route("/api/task/dependencies/<int:dep_id>", methods=["DELETE"])
@login_required
def delete_dependency(dep_id: int) -> Any:
    """删除依赖关系"""
    try:
        dep = db.session.get(TaskDependency, dep_id)
        if not dep:
            return error_response("依赖关系不存在", code=400)

        db.session.delete(dep)
        db.session.commit()
        logger.info(f"[Dep] 删除依赖 ID={dep_id}")
        return success_response("依赖关系已删除")

    except Exception as e:
        logger.error(f"[Dep] 删除依赖异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("删除依赖失败", code=500)


@task_bp.route("/api/task/dependencies/graph", methods=["GET"])
@login_required
def get_dependency_graph() -> Any:
    """
    获取所有任务的依赖关系图（用于前端可视化）
    返回：nodes + edges
    """
    try:
        tasks = ScriptTask.query.all()
        deps = TaskDependency.query.all()

        nodes = [{"id": t.id, "name": t.task_name, "is_active": t.is_active} for t in tasks]
        edges = [
            {
                "id": d.id,
                "from": d.upstream_task_id,
                "to": d.downstream_task_id,
                "is_active": d.is_active,
            }
            for d in deps
        ]

        return success_response(data={"nodes": nodes, "edges": edges})

    except Exception as e:
        logger.error(f"[Dep] 获取依赖图异常: {e}", exc_info=True)
        return error_response("获取依赖图失败", code=500)


def _has_circular_dependency(upstream_id: int, downstream_id: int) -> bool:
    """
    检测是否会形成循环依赖
    检查从 downstream 出发是否能通过依赖链回到 upstream
    """
    all_deps = TaskDependency.query.all()
    adjacency = {}
    for dep in all_deps:
        adjacency.setdefault(dep.upstream_task_id, []).append(dep.downstream_task_id)

    visited = set()
    queue = [downstream_id]

    while queue:
        current = queue.pop(0)
        if current == upstream_id:
            return True  # 形成循环
        if current in visited:
            continue
        visited.add(current)
        for next_id in adjacency.get(current, []):
            queue.append(next_id)

    return False


# ============================================================
# 系统配置 API（短信等）
# ============================================================

@task_bp.route("/api/system-config", methods=["GET"])
@login_required
def get_system_config() -> Any:
    """获取所有系统配置"""
    try:
        if current_user.role != ROLE_ADMIN:
            return error_response("没有权限", code=403)

        from app.services.sms_service import get_all_sms_config
        config = get_all_sms_config()
        return success_response(data=config)

    except Exception as e:
        logger.error(f"[Config] 获取系统配置异常: {e}", exc_info=True)
        return error_response("获取配置失败", code=500)


@task_bp.route("/api/system-config", methods=["POST"])
@login_required
def update_system_config() -> Any:
    """
    批量更新系统配置
    请求体 JSON：{key: value, ...}
    """
    try:
        if current_user.role != ROLE_ADMIN:
            return error_response("没有权限", code=403)

        data = request.get_json(silent=True) or {}
        from app.services.sms_service import set_config_value

        count = 0
        for key, value in data.items():
            set_config_value(key, str(value))
            count += 1

        logger.info(f"[Config] 更新 {count} 项系统配置")
        return success_response(f"已更新 {count} 项配置")

    except Exception as e:
        logger.error(f"[Config] 更新系统配置异常: {e}", exc_info=True)
        return error_response("更新配置失败", code=500)


@task_bp.route("/api/system-config/test-sms", methods=["POST"])
@login_required
def test_sms() -> Any:
    """发送测试短信"""
    try:
        if current_user.role != ROLE_ADMIN:
            return error_response("没有权限", code=403)

        from app.services.sms_service import send_failure_sms
        result = send_failure_sms("测试任务", "这是一条测试消息", "")
        if result:
            return success_response("测试短信发送成功")
        return error_response("测试短信发送失败，请检查配置", code=400)

    except Exception as e:
        logger.error(f"[Config] 测试短信异常: {e}", exc_info=True)
        return error_response("测试短信失败", code=500)


# ============================================================
# 短信记录接口
# ============================================================


@task_bp.route("/sms-records")
@login_required
def sms_records_page() -> str:
    """短信记录页面"""
    return render_template("sms/records.html")


@task_bp.route("/api/sms-records", methods=["GET"])
@login_required
def get_sms_records() -> Any:
    """
    获取短信记录（分页+搜索）
    参数：page, per_page, keyword
    """
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(100, max(1, int(request.args.get("per_page", 15))))
        keyword = (request.args.get("keyword") or "").strip()

        from app.services.sms_service import get_sms_records as _get_records
        data = _get_records(page=page, per_page=per_page, keyword=keyword)
        return success_response(data=data)
    except Exception as e:
        logger.error(f"[SMS-Record] 查询失败: {e}", exc_info=True)
        return error_response("查询短信记录失败", code=500)


@task_bp.route("/api/sms-records/export", methods=["GET"])
@login_required
def export_sms_records() -> Any:
    """导出短信记录为 CSV"""
    from flask import Response
    from datetime import datetime

    try:
        keyword = (request.args.get("keyword") or "").strip().lower()

        from app.services.sms_service import get_all_sms_records
        records = get_all_sms_records()
        if keyword:
            def match(r):
                body_str = json.dumps(r.get("body", {}), ensure_ascii=False).lower()
                time_str = r.get("time", "").lower()
                return keyword in body_str or keyword in time_str
            records = [r for r in records if match(r)]

        lines = ["ID,时间,状态,手机号,内容,任务名"]
        for r in records:
            body = r.get("body", {})
            phones = body.get("phones", "")
            content = body.get("content", "")
            task_name = body.get("task_name", "")

            def esc(v):
                s = str(v).replace('"', '""')
                return f'"{s}"'
            lines.append(",".join([
                str(r.get("id", "")),
                esc(r.get("time", "")),
                esc(r.get("status", "")),
                esc(phones),
                esc(content),
                esc(task_name),
            ]))

        csv_content = "\n".join(lines)
        filename = f"sms_records_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            "\ufeff" + csv_content,
            mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    except Exception as e:
        logger.error(f"[SMS-Record] 导出失败: {e}", exc_info=True)
        return error_response("导出失败", code=500)


@task_bp.route("/api/sms-records/clear", methods=["POST"])
@login_required
def clear_sms_records() -> Any:
    """清空短信记录"""
    from app.services.sms_service import clear_sms_records as _clear
    _clear()
    return success_response("已清空短信记录")


@task_bp.route("/api/mock-sms", methods=["POST"])
def mock_sms_receive() -> Any:
    """模拟短信接收接口（短信服务调用此接口）"""
    try:
        body = request.get_json(silent=True) or {}
        from app.services.sms_service import _add_sms_record
        task_name = body.get("task_name", "")
        phones = body.get("phones", "")
        content = body.get("content", "")
        _add_sms_record(task_name, phones, content, "mock")
        logger.info(f"[SMS] 收到模拟短信: {json.dumps(body, ensure_ascii=False)[:200]}")
        return success_response("短信发送成功（模拟）")
    except Exception as e:
        logger.error(f"[SMS] 接收异常: {e}", exc_info=True)
        return error_response("短信接收失败", code=500)