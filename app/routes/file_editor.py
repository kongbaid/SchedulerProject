"""
文件编辑器模块路由
提供在线文件管理功能（仅超级管理员可访问）
"""
import os
import io
import shutil
import logging
import subprocess
import sys
import time
import threading
from typing import Any, List, Dict
from pathlib import Path
from datetime import datetime

from flask import Blueprint, request, render_template, jsonify, current_app
from flask_login import login_required, current_user

from app.utils.response import success_response, error_response
from app.utils.process import kill_process_tree
from app.models.user import ROLE_ADMIN
from app.models.project import SavedProject
from app.models.editor_execution import EditorExecutionLog
from app.extensions import db
try:
    from config import Config
except ImportError:
    from config_example import Config

logger = logging.getLogger(__name__)

file_editor_bp = Blueprint('file_editor', __name__, url_prefix='/file-editor')

# 文件编辑器脚本运行状态（按执行日志ID存储）
# 结构: { execution_id: { 'proc': Popen, 'status': 'running'|'done', 'result': {...} } }
_running_scripts: Dict[int, Dict] = {}
_scripts_lock = threading.Lock()


def _build_state_output(state: Dict) -> str:
    """从 state 中的缓冲区引用构建输出字符串（延迟构建，避免每行输出都拼接）"""
    if not state.get('_output_dirty'):
        return state.get('output', '')
    head_buf = state.get('_head_buf')
    tail_lines = state.get('_tail_lines')
    output_truncated = state.get('_output_truncated', False)
    total_output_len = state.get('_total_output_len', 0)
    if head_buf is None:
        return state.get('output', '')
    if not output_truncated:
        return head_buf.getvalue()
    head_text = head_buf.getvalue()
    tail_text = ''.join(tail_lines) if tail_lines else ''
    truncated_len = total_output_len - len(head_text) - len(tail_text)
    return (
        head_text
        + f'\n...[已截断 {truncated_len} 字节，保留头部和尾部]...\n'
        + tail_text
    )

# ============================================================
# 项目独占锁（防止多用户同时修改同一项目）
# ============================================================
# 结构: { norm_path: { 'username': str, 'user_id': int, 'session_token': str,
#                       'locked_at': float, 'last_heartbeat': float } }
_project_locks: Dict[str, Dict] = {}
_project_locks_lock = threading.Lock()
_LOCK_TIMEOUT_SECONDS = 180  # 3分钟无心跳自动过期
_last_lock_cleanup_time = 0.0


def _start_lock_cleanup_timer():
    """后台定时清理过期锁和过期执行日志，防止内存泄漏（每5分钟执行一次）"""
    def _loop():
        while True:
            time.sleep(300)
            try:
                _cleanup_expired_locks(force=True)
            except Exception:
                pass
            try:
                cleanup_old_execution_logs()
            except Exception:
                pass
    t = threading.Thread(target=_loop, daemon=True, name='lock-cleanup')
    t.start()

_start_lock_cleanup_timer()

# 配置：允许访问的根目录列表（可在 config.py 中配置）
# 安全建议：生产环境根据需要调整，不要开放整个磁盘
ALLOWED_ROOT_DIRS = [
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')),  # 项目根目录
    os.path.abspath(os.path.expanduser('~')),  # 用户主目录（如 C:\Users\Administrator）

    'D:\\',  # D盘根目录
    'C:\\',  # C盘根目录（开放整个C盘，谨慎使用）
    'E:\\',  # E盘根目录
    'F:\\',  # F盘根目录
]


def is_admin_required(f):
    """装饰器：仅允许超级管理员访问"""
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.role != ROLE_ADMIN:
            return error_response("仅超级管理员可访问此功能", code=403)
        return f(*args, **kwargs)
    
    return decorated_function


def validate_path_security(file_path: str, base_dir: str) -> bool:
    """
    验证路径安全性，防止路径穿越攻击
    """
    abs_path = os.path.normcase(os.path.abspath(file_path))
    abs_base = os.path.normcase(os.path.abspath(base_dir))
    
    # 确保 base 目录以分隔符结尾，避免 /path/a 匹配 /path/abc
    if not abs_base.endswith(os.sep):
        abs_base += os.sep
    
    # 精确匹配：路径完全等于 base（访问根目录本身）或以 base/ 开头
    if abs_path == abs_base.rstrip(os.sep) or abs_path.startswith(abs_base):
        pass
    else:
        return False
    
    # 禁止访问危险目录
    dangerous_patterns = ['__pycache__', '.git', 'node_modules', '.env']
    for pattern in dangerous_patterns:
        if pattern in abs_path:
            return False
    
    return True


def _norm_lock_path(path: str) -> str:
    """规范化路径作为锁的 key"""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def _cleanup_expired_locks(force: bool = False) -> None:
    """清理已过期的项目锁（至少间隔60秒执行一次，除非 force=True）"""
    global _last_lock_cleanup_time
    now = time.monotonic()
    if not force and now - _last_lock_cleanup_time < 60:
        return
    with _project_locks_lock:
        if not force and now - _last_lock_cleanup_time < 60:
            return
        expired = [
            p for p, info in _project_locks.items()
            if now - info.get('last_heartbeat', 0) > _LOCK_TIMEOUT_SECONDS
        ]
        for p in expired:
            info = _project_locks.pop(p, None)
            if info:
                logger.info(f"[ProjectLock] 锁已过期自动释放: {p} (用户: {info.get('username')})")
        _last_lock_cleanup_time = now


def _get_project_lock(project_path: str) -> Dict:
    """
    获取项目锁信息
    :return: {'locked': bool, 'username': str|None, 'is_self': bool}
    """
    _cleanup_expired_locks()
    norm = _norm_lock_path(project_path)
    with _project_locks_lock:
        info = _project_locks.get(norm)
        if not info:
            return {'locked': False, 'username': None, 'is_self': False}
        return {
            'locked': True,
            'username': info.get('username'),
            'is_self': info.get('user_id') == current_user.id,
        }


def _check_file_locked(file_path: str, session_token: str = '') -> bool:
    """
    检查文件所在项目是否被锁定（不允许写入）
    - 不同用户锁定 → 拒绝
    - 同用户但 session_token 不匹配（被同账号其他会话接管）→ 拒绝
    :return: True=被锁定（不允许写入）
    """
    _cleanup_expired_locks()
    norm_file = os.path.normcase(os.path.normpath(os.path.abspath(file_path)))
    with _project_locks_lock:
        for proj_path, info in _project_locks.items():
            if norm_file.startswith(proj_path + os.sep) or norm_file == proj_path:
                if info.get('user_id') != current_user.id:
                    return True
                if session_token and info.get('session_token') and \
                        info.get('session_token') != session_token:
                    return True
                return False
    return False


def get_file_tree(directory: str, base_dir: str) -> List[Dict]:
    """
    获取目录文件树（仅一层，用于懒加载）
    排序规则：文件夹优先，然后按字母顺序排序
    
    :param directory: 当前目录
    :param base_dir: 基础目录（安全检查用）
    """
    tree = []
    
    try:
        entries = sorted(os.listdir(directory))
    except PermissionError:
        return tree
    
    # 分离文件夹和文件
    folders = []
    files = []
    
    for entry in entries:
        full_path = os.path.join(directory, entry)
        
        # 跳过隐藏文件和安全检查
        if entry.startswith('.') or not validate_path_security(full_path, base_dir):
            continue
        
        if os.path.isdir(full_path):
            folders.append((entry, full_path))
        else:
            files.append((entry, full_path))
    
    # 先处理文件夹（已按字母排序）
    for entry, full_path in folders:
        node = {
            'name': entry,
            'path': full_path,
            'type': 'folder',
            'children': None,
        }

        try:
            sub_entries = os.listdir(full_path)
            has_visible = any(
                not e.startswith('.')
                for e in sub_entries
            )
            node['has_children'] = has_visible
        except (PermissionError, OSError):
            node['has_children'] = False
        
        tree.append(node)
    
    # 再处理文件（已按字母排序）
    for entry, full_path in files:
        node = {
            'name': entry,
            'path': full_path,
            'type': 'file',
            'size': os.path.getsize(full_path),
            'extension': os.path.splitext(entry)[1].lower()
        }
        tree.append(node)
    
    return tree


# ============================================================
# 页面路由
# ============================================================

@file_editor_bp.route('/')
@login_required
@is_admin_required
def file_editor_page() -> str:
    """文件编辑器主页面"""
    return render_template('file-editor/index.html')


# ============================================================
# API 接口
# ============================================================

@file_editor_bp.route('/api/init', methods=['POST'])
@login_required
@is_admin_required
def init_project() -> Any:
    """
    初始化项目路径
    请求体 JSON：project_path, save_project (可选，是否保存)
    """
    try:
        data = request.get_json(silent=True) or {}
        project_path = (data.get('project_path') or '').strip()
        save_project = data.get('save_project', False)
        project_name = (data.get('project_name') or '').strip()
        description = (data.get('description') or '').strip()
        
        if not project_path:
            return error_response("项目路径不能为空", code=400)
        
        # 规范化路径
        project_path = os.path.abspath(project_path)
        logger.info(f"[FileEditor] 规范化后的项目路径: {project_path}")
        logger.info(f"[FileEditor] 允许的根目录列表: {ALLOWED_ROOT_DIRS}")
        
        # 检查路径是否存在
        if not os.path.exists(project_path):
            return error_response("路径不存在", code=400)
        
        if not os.path.isdir(project_path):
            return error_response("路径不是文件夹", code=400)
        
        # 安全检查
        allowed = False
        matched_dir = None
        for root_dir in ALLOWED_ROOT_DIRS:
            # 确保路径分隔符一致
            norm_root_dir = os.path.normpath(root_dir)
            norm_project_path = os.path.normpath(project_path)
            
            logger.debug(f"[FileEditor] 比较: '{norm_project_path}' vs '{norm_root_dir}'")
            
            # 检查路径是否以允许的根目录开头（不区分大小写）
            if norm_project_path.lower().startswith(norm_root_dir.lower()):
                allowed = True
                matched_dir = root_dir
                break
        
        if not allowed:
            logger.warning(f"[FileEditor] 路径安全检查失败: {project_path} 不在允许的范围内")
            logger.warning(f"[FileEditor] 允许的根目录: {ALLOWED_ROOT_DIRS}")
            return error_response("该路径不在允许访问的范围内", code=403)
        
        logger.info(f"[FileEditor] 路径安全检查通过: 匹配到 {matched_dir}")
        
        # 如果要求保存项目
        if save_project:
            if not project_name:
                project_name = os.path.basename(project_path)
            
            # 检查是否已存在
            existing = SavedProject.query.filter_by(project_path=project_path).first()
            if existing:
                # 更新访问信息
                existing.last_accessed_at = datetime.now()
                existing.access_count += 1
            else:
                # 创建新项目
                new_project = SavedProject(
                    project_name=project_name,
                    project_path=project_path,
                    description=description if description else None,
                    last_accessed_at=datetime.now(),
                    access_count=1
                )
                db.session.add(new_project)
            
            db.session.commit()
        
        logger.info(f"[FileEditor] 管理员 '{current_user.username}' 访问项目: {project_path}")
        
        return success_response("项目初始化成功", data={
            'project_path': project_path,
            'project_name': os.path.basename(project_path)
        })
    
    except Exception as e:
        logger.error(f"[FileEditor] 初始化项目异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("初始化失败", code=500)


@file_editor_bp.route('/api/lock', methods=['POST'])
@login_required
@is_admin_required
def lock_project() -> Any:
    """
    获取项目独占锁
    请求体 JSON：project_path, session_token（前端生成的会话标识）,
                 force_takeover（可选，强制接管同账号其他会话的锁）
    返回：200=获取成功（可能含 same_session 警告），409=已被其他用户锁定
    """
    try:
        data = request.get_json(silent=True) or {}
        project_path = (data.get('project_path') or '').strip()
        session_token = (data.get('session_token') or '').strip()
        force_takeover = data.get('force_takeover', False)
        if not project_path:
            return error_response("项目路径不能为空", code=400)
        
        norm = _norm_lock_path(project_path)
        _cleanup_expired_locks()
        
        with _project_locks_lock:
            existing = _project_locks.get(norm)
            if existing:
                if existing.get('user_id') != current_user.id:
                    return error_response(
                        f"项目已被用户 '{existing.get('username')}' 锁定，请等待其关闭后再打开",
                        code=409,
                        data={'locked_by': existing.get('username')}
                    )
                if existing.get('session_token') and session_token \
                        and existing.get('session_token') != session_token:
                    if not force_takeover:
                        return success_response(
                            "项目已被同账号其他会话锁定",
                            data={'same_session': True}
                        )
                    logger.warning(
                        f"[ProjectLock] 同一账号多会话锁定: 用户 '{current_user.username}' "
                        f"接管项目 {project_path}（旧会话: {existing.get('session_token')[:8]}...）"
                    )
                    _project_locks[norm] = {
                        'username': current_user.username,
                        'user_id': current_user.id,
                        'session_token': session_token,
                        'locked_at': time.monotonic(),
                        'last_heartbeat': time.monotonic(),
                    }
                    return success_response("项目已锁定", data={'same_session': True, 'taken_over': True})
            
            _project_locks[norm] = {
                'username': current_user.username,
                'user_id': current_user.id,
                'session_token': session_token,
                'locked_at': time.monotonic(),
                'last_heartbeat': time.monotonic(),
            }
        
        logger.info(f"[ProjectLock] 用户 '{current_user.username}' 锁定项目: {project_path}")
        return success_response("项目已锁定")
    
    except Exception as e:
        logger.error(f"[ProjectLock] 锁定项目异常: {e}", exc_info=True)
        return error_response("锁定项目失败", code=500)


@file_editor_bp.route('/api/unlock', methods=['POST'])
@login_required
@is_admin_required
def unlock_project() -> Any:
    """
    释放项目独占锁
    请求体 JSON：project_path
    """
    try:
        data = request.get_json(silent=True) or {}
        project_path = (data.get('project_path') or '').strip()
        if not project_path:
            return error_response("项目路径不能为空", code=400)
        
        norm = _norm_lock_path(project_path)
        with _project_locks_lock:
            info = _project_locks.get(norm)
            if info and info.get('user_id') == current_user.id:
                del _project_locks[norm]
                logger.info(f"[ProjectLock] 用户 '{current_user.username}' 释放项目锁: {project_path}")
        
        return success_response("项目已解锁")
    
    except Exception as e:
        logger.error(f"[ProjectLock] 解锁项目异常: {e}", exc_info=True)
        return error_response("解锁项目失败", code=500)


@file_editor_bp.route('/api/lock/heartbeat', methods=['POST'])
@login_required
@is_admin_required
def lock_heartbeat() -> Any:
    """
    锁心跳（保持锁活跃，防止自动过期）
    请求体 JSON：project_path, session_token（可选）
    """
    try:
        data = request.get_json(silent=True) or {}
        project_path = (data.get('project_path') or '').strip()
        session_token = (data.get('session_token') or '').strip()
        if not project_path:
            return error_response("项目路径不能为空", code=400)
        
        norm = _norm_lock_path(project_path)
        with _project_locks_lock:
            info = _project_locks.get(norm)
            if info and info.get('user_id') == current_user.id:
                # 检查 session_token 是否匹配（判断是否被同账号其他会话接管）
                if session_token and info.get('session_token') and \
                        info.get('session_token') != session_token:
                    return error_response(
                        "您的编辑权已被同一账号的其他会话接管",
                        code=409,
                        data={'lock_taken': True}
                    )
                info['last_heartbeat'] = time.monotonic()
                if session_token:
                    info['session_token'] = session_token
                return success_response("心跳已更新")
            elif info:
                return error_response("项目已被其他用户锁定", code=409)
            else:
                # 锁已过期或不存在，重新获取
                _project_locks[norm] = {
                    'username': current_user.username,
                    'user_id': current_user.id,
                    'session_token': session_token,
                    'locked_at': time.monotonic(),
                    'last_heartbeat': time.monotonic(),
                }
                return success_response("锁已重新获取")
    
    except Exception as e:
        logger.error(f"[ProjectLock] 心跳异常: {e}", exc_info=True)
        return error_response("心跳失败", code=500)


@file_editor_bp.route('/api/projects', methods=['GET'])
@login_required
@is_admin_required
def get_projects() -> Any:
    """
    获取已保存的项目列表
    """
    try:
        # 按访问次数降序，最近访问的优先
        projects = SavedProject.query.order_by(
            SavedProject.access_count.desc(),
            SavedProject.last_accessed_at.desc()
        ).all()
        
        return success_response(data={
            'projects': [p.to_dict() for p in projects]
        })
    
    except Exception as e:
        logger.error(f"[FileEditor] 获取项目列表异常: {e}", exc_info=True)
        return error_response("获取项目列表失败", code=500)


@file_editor_bp.route('/api/project/<int:project_id>', methods=['DELETE'])
@login_required
@is_admin_required
def delete_project(project_id: int) -> Any:
    """
    删除已保存的项目
    """
    try:
        project = db.session.get(SavedProject, project_id)
        if not project:
            return error_response("项目不存在", code=400)
        
        db.session.delete(project)
        db.session.commit()
        
        logger.info(f"[FileEditor] 删除项目记录: {project.project_name}")
        return success_response("项目已删除")
    
    except Exception as e:
        logger.error(f"[FileEditor] 删除项目异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("删除项目失败", code=500)


@file_editor_bp.route('/api/project/<int:project_id>', methods=['PUT'])
@login_required
@is_admin_required
def update_project(project_id: int) -> Any:
    """
    更新项目信息
    请求体 JSON：project_name, project_path, description
    """
    try:
        project = db.session.get(SavedProject, project_id)
        if not project:
            return error_response("项目不存在", code=400)
        
        data = request.get_json(silent=True) or {}
        project_name = (data.get('project_name') or '').strip()
        project_path = (data.get('project_path') or '').strip()
        description = (data.get('description') or '').strip()
        
        if not project_name:
            return error_response("项目名称不能为空", code=400)
        
        if not project_path:
            return error_response("项目路径不能为空", code=400)
        
        # 检查路径是否与其他项目冲突
        existing = SavedProject.query.filter(
            SavedProject.project_path == project_path,
            SavedProject.id != project_id
        ).first()
        if existing:
            return error_response("该路径已被其他项目使用", code=400)
        
        project.project_name = project_name
        project.project_path = project_path
        project.description = description if description else None
        db.session.commit()
        
        logger.info(f"[FileEditor] 更新项目: {project.project_name}")
        return success_response("项目已更新", data={'project': project.to_dict()})
    
    except Exception as e:
        logger.error(f"[FileEditor] 更新项目异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("更新项目失败", code=500)


@file_editor_bp.route('/api/project', methods=['POST'])
@login_required
@is_admin_required
def create_project() -> Any:
    """
    创建新项目
    请求体 JSON：project_name, project_path, description
    """
    try:
        data = request.get_json(silent=True) or {}
        project_name = (data.get('project_name') or '').strip()
        project_path = (data.get('project_path') or '').strip()
        description = (data.get('description') or '').strip()
        
        if not project_name:
            return error_response("项目名称不能为空", code=400)
        
        if not project_path:
            return error_response("项目路径不能为空", code=400)
        
        if not os.path.isdir(project_path):
            return error_response("项目路径不存在", code=400)
        
        # 检查路径是否已存在
        existing = SavedProject.query.filter_by(project_path=project_path).first()
        if existing:
            return error_response("该路径已存在项目中", code=400)
        
        # 创建新项目
        new_project = SavedProject(
            project_name=project_name,
            project_path=project_path,
            description=description if description else None,
            access_count=0
        )
        db.session.add(new_project)
        db.session.commit()
        
        logger.info(f"[FileEditor] 创建项目: {project_name}")
        return success_response("项目创建成功", data={'project': new_project.to_dict()})
    
    except Exception as e:
        logger.error(f"[FileEditor] 创建项目异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("创建项目失败", code=500)


@file_editor_bp.route('/api/tree', methods=['POST'])
@login_required
@is_admin_required
def get_tree() -> Any:
    """
    获取文件树（懒加载模式，每次只返回一层）
    请求体 JSON：project_path, sub_dir（可选，加载子目录）
    """
    try:
        data = request.get_json(silent=True) or {}
        project_path = data.get('project_path', '').strip()
        sub_dir = data.get('sub_dir', '').strip()
        
        if not project_path or not os.path.isdir(project_path):
            return error_response("项目路径无效", code=400)
        
        # 如果指定了子目录，加载子目录
        target_dir = project_path
        if sub_dir:
            target_dir = os.path.abspath(sub_dir)
            # 安全检查：确保子目录在项目路径内
            norm_target = os.path.normcase(os.path.normpath(target_dir))
            norm_project = os.path.normcase(os.path.normpath(project_path))
            if not norm_target.startswith(norm_project):
                return error_response("子目录不在项目路径内", code=403)
            if not os.path.isdir(target_dir):
                return error_response("子目录不存在", code=400)
        
        tree = get_file_tree(target_dir, project_path)
        
        return success_response(data={
            'tree': tree,
            'directory': target_dir,
            'project_path': project_path,
        })
    
    except Exception as e:
        logger.error(f"[FileEditor] 获取文件树异常: {e}", exc_info=True)
        return error_response("获取文件树失败", code=500)


@file_editor_bp.route('/api/file/read', methods=['POST'])
@login_required
@is_admin_required
def read_file() -> Any:
    """
    读取文件内容
    请求体 JSON：file_path
    """
    try:
        data = request.get_json(silent=True) or {}
        file_path = data.get('file_path', '').strip()
        
        if not file_path:
            return error_response("文件路径不能为空", code=400)
        
        if not os.path.isfile(file_path):
            return error_response("文件不存在", code=400)
        
        # 检查文件大小（限制 1MB）
        file_size = os.path.getsize(file_path)
        if file_size > Config.EDITOR_MAX_FILE_SIZE:
            return error_response("文件过大（超过1MB）", code=400)
        
        # 读取文件（尝试多种编码）
        try:
            content = None
            # 尝试的编码列表（按优先级）
            encodings = ['utf-8', 'gbk', 'gb2312', 'gb18030', 'latin-1']
            
            for encoding in encodings:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        content = f.read()
                    logger.info(f"[FileEditor] 使用 {encoding} 编码成功读取文件: {file_path}")
                    break
                except UnicodeDecodeError:
                    continue
            
            if content is None:
                return error_response("文件不是文本格式（无法解码）", code=400)
            
            return success_response(data={
                'content': content,
                'size': file_size,
                'path': file_path
            })
        except PermissionError:
            return error_response("没有权限读取此文件", code=403)
    
    except Exception as e:
        logger.error(f"[FileEditor] 读取文件异常: {e}", exc_info=True)
        return error_response("读取文件失败", code=500)


@file_editor_bp.route('/api/file/save', methods=['POST'])
@login_required
@is_admin_required
def save_file() -> Any:
    """
    保存文件内容
    请求体 JSON：file_path, content
    """
    try:
        data = request.get_json(silent=True) or {}
        file_path = data.get('file_path', '').strip()
        content = data.get('content', '')
        
        session_token = (data.get('session_token') or '').strip()
        
        if not file_path:
            return error_response("文件路径不能为空", code=400)
        
        # 锁检查：项目是否被其他用户锁定（或编辑权已被接管）
        if _check_file_locked(file_path, session_token):
            return error_response("该项目已被锁定或编辑权已被接管，无法保存文件", code=409)
        
        # 如果文件不存在，创建它
        if not os.path.isfile(file_path):
            # 确保目录存在
            dir_path = os.path.dirname(file_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
        
        # 写入文件（统一使用UTF-8编码）
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            logger.info(f"[FileEditor] 保存文件成功: {file_path} (UTF-8编码)")
            
            # 如果是 Python 文件，检查语法
            syntax_check = None
            if file_path.lower().endswith('.py'):
                import py_compile
                import tempfile
                try:
                    # 将内容写入临时文件进行语法检查
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', encoding='utf-8', delete=False) as tmp:
                        tmp.write(content)
                        tmp_path = tmp.name
                    py_compile.compile(tmp_path, doraise=True)
                    os.unlink(tmp_path)
                    syntax_check = {'valid': True}
                except py_compile.PyCompileError as e:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                    # 提取错误信息
                    error_msg = str(e).strip()
                    # 尝试提取行号和错误描述
                    line_num = None
                    error_desc = error_msg
                    if 'line' in error_msg.lower():
                        import re
                        match = re.search(r'line\s+(\d+)', error_msg, re.IGNORECASE)
                        if match:
                            line_num = int(match.group(1))
                        # 提取 SyntaxError 后的描述
                        syn_match = re.search(r'SyntaxError:\s*(.+)', error_msg)
                        if syn_match:
                            error_desc = syn_match.group(1).strip()
                    syntax_check = {
                        'valid': False,
                        'line': line_num,
                        'message': error_desc
                    }
            
            return success_response("文件保存成功", data={'syntax_check': syntax_check})
        except UnicodeEncodeError as e:
            logger.error(f"[FileEditor] 编码错误: {e}")
            return error_response(f"文件包含无法编码的字符: {str(e)}", code=400)
        except IOError as e:
            logger.error(f"[FileEditor] IO错误: {e}")
            return error_response(f"写入文件失败: {str(e)}", code=500)
    
    except PermissionError:
        return error_response("没有权限写入此文件", code=403)
    except Exception as e:
        logger.error(f"[FileEditor] 保存文件异常: {e}", exc_info=True)
        return error_response("保存文件失败", code=500)


@file_editor_bp.route('/api/file/create', methods=['POST'])
@login_required
@is_admin_required
def create_file() -> Any:
    """
    创建文件或文件夹
    请求体 JSON：path, type (file/folder)
    """
    try:
        data = request.get_json(silent=True) or {}
        path = data.get('path', '').strip()
        type_ = data.get('type', 'file')
        session_token = (data.get('session_token') or '').strip()
        
        if not path:
            return error_response("路径不能为空", code=400)
        
        # 锁检查
        if _check_file_locked(path, session_token):
            return error_response("该项目已被锁定或编辑权已被接管，无法创建文件", code=409)
        
        if type_ == 'folder':
            if os.path.exists(path):
                return error_response("文件夹已存在", code=400)
            os.makedirs(path, exist_ok=True)
            logger.info(f"[FileEditor] 创建文件夹: {path}")
            return success_response("文件夹创建成功")
        else:
            if os.path.exists(path):
                return error_response("文件已存在", code=400)
            
            # 确保目录存在
            dir_path = os.path.dirname(path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            
            # 创建空文件
            with open(path, 'w', encoding='utf-8') as f:
                pass
            
            logger.info(f"[FileEditor] 创建文件: {path}")
            return success_response("文件创建成功")
    
    except Exception as e:
        logger.error(f"[FileEditor] 创建异常: {e}", exc_info=True)
        return error_response("创建失败", code=500)


@file_editor_bp.route('/api/file/delete', methods=['POST'])
@login_required
@is_admin_required
def delete_file() -> Any:
    """
    删除文件或文件夹
    请求体 JSON：path, type (file/folder)
    """
    try:
        data = request.get_json(silent=True) or {}
        path = data.get('path', '').strip()
        type_ = data.get('type', 'file')
        session_token = (data.get('session_token') or '').strip()
        
        if not path:
            return error_response("路径不能为空", code=400)
        
        # 锁检查
        if _check_file_locked(path, session_token):
            return error_response("该项目已被锁定或编辑权已被接管，无法删除文件", code=409)
        
        if not os.path.exists(path):
            return error_response("路径不存在", code=400)
        
        if type_ == 'folder':
            shutil.rmtree(path)
            logger.info(f"[FileEditor] 删除文件夹: {path}")
        else:
            os.remove(path)
            logger.info(f"[FileEditor] 删除文件: {path}")
        
        return success_response("删除成功")
    
    except Exception as e:
        logger.error(f"[FileEditor] 删除异常: {e}", exc_info=True)
        return error_response("删除失败", code=500)


@file_editor_bp.route('/api/file/rename', methods=['POST'])
@login_required
@is_admin_required
def rename_file() -> Any:
    """
    重命名文件或文件夹
    请求体 JSON：old_path, new_path
    """
    try:
        data = request.get_json(silent=True) or {}
        old_path = data.get('old_path', '').strip()
        new_path = data.get('new_path', '').strip()
        session_token = (data.get('session_token') or '').strip()
        
        if not old_path or not new_path:
            return error_response("路径不能为空", code=400)
        
        # 锁检查
        if _check_file_locked(old_path, session_token):
            return error_response("该项目已被锁定或编辑权已被接管，无法重命名文件", code=409)
        
        if not os.path.exists(old_path):
            return error_response("原路径不存在", code=400)
        
        if os.path.exists(new_path):
            return error_response("新路径已存在", code=400)
        
        os.rename(old_path, new_path)
        logger.info(f"[FileEditor] 重命名: {old_path} -> {new_path}")
        
        return success_response("重命名成功")
    
    except Exception as e:
        logger.error(f"[FileEditor] 重命名异常: {e}", exc_info=True)
        return error_response("重命名失败", code=500)


@file_editor_bp.route('/api/file/run', methods=['POST'])
@login_required
@is_admin_required
def run_script() -> Any:
    """
    启动Python脚本（异步执行，立即返回）
    执行记录保存到数据库，可在执行列表中查询
    """
    try:
        data = request.get_json(silent=True) or {}
        file_path = data.get('file_path', '').strip()
        python_path = data.get('python_path', 'python').strip()
        project_path = data.get('project_path', '').strip()
        timeout_override = data.get('timeout')
        
        if not file_path:
            return error_response("文件路径不能为空", code=400)
        if not os.path.isfile(file_path):
            return error_response("文件不存在", code=400)
        if not file_path.lower().endswith('.py'):
            return error_response("只能运行Python脚本(.py文件)", code=400)
        if not python_path:
            python_path = 'python'
        
        user_id = current_user.id

        max_concurrent = current_app.config.get('EDITOR_MAX_CONCURRENT_SCRIPTS', 5)
        with _scripts_lock:
            user_running = sum(
                1 for s in _running_scripts.values()
                if s.get('user_id') == user_id and s.get('status') == 'running'
            )
        if user_running >= max_concurrent:
            return error_response(
                f"同时运行脚本数已达上限（{max_concurrent}），请等待其他脚本完成后再试",
                code=400
            )

        # 构建子进程
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUTF8'] = '1'
        
        # 将项目根目录加入 PYTHONPATH，使脚本能导入项目根目录下的模块
        extra_paths = []
        if project_path and os.path.isdir(project_path):
            extra_paths.append(os.path.abspath(project_path))
        app_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        if app_root not in extra_paths:
            extra_paths.append(app_root)
        if extra_paths:
            existing = env.get('PYTHONPATH', '')
            sep = os.pathsep
            env['PYTHONPATH'] = sep.join(extra_paths) + (sep + existing if existing else '')
        
        try:
            proc = subprocess.Popen(
                [python_path, '-u', file_path],
                cwd=os.path.dirname(file_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # 合并 stderr 到 stdout
                env=env
            )
        except FileNotFoundError:
            return error_response(f"找不到Python解释器: {python_path}", code=400)
        except Exception as e:
            return error_response(f"启动脚本失败: {str(e)}", code=500)
        
        # 创建数据库执行记录
        execution_log = EditorExecutionLog(
            user_id=user_id,
            script_path=file_path,
            python_path=python_path,
            status='running',
            pid=proc.pid,
            start_time=datetime.now(),
        )
        db.session.add(execution_log)
        db.session.commit()
        
        execution_id = execution_log.id
        
        # 存储运行状态（output 字段供实时轮询显示）
        state = {
            'proc': proc,
            'status': 'running',
            'output': '',
            'result': None,
            'start_time': time.monotonic(),
            'execution_id': execution_id,
            'user_id': user_id,
            '_stop_requested': False,
            '_timeout_override': timeout_override,
        }
        with _scripts_lock:
            _running_scripts[execution_id] = state
        
        # 启动后台线程读取输出（传递自定义超时时间）
        thread = threading.Thread(
            target=_read_script_output,
            args=(execution_id, proc, timeout_override),
            daemon=True,
            name=f'editor-script-{execution_id}'
        )
        thread.start()
        
        logger.info(f"[FileEditor] 脚本已启动: PID={proc.pid}, 用户ID={user_id}, 执行ID={execution_id}")
        return success_response(data={'execution_id': execution_id, 'pid': proc.pid})
    
    except Exception as e:
        logger.error(f"[FileEditor] 运行脚本异常: {e}", exc_info=True)
        return error_response("运行脚本失败", code=500)


def _decode_output(data) -> str:
    """解码输出（尝试 utf-8 -> gbk -> gb18030 -> latin-1）"""
    if not data:
        return ''
    if isinstance(data, str):
        return data
    for enc in ('utf-8', 'gbk', 'gb18030', 'latin-1'):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, AttributeError):
            continue
    return data.decode('utf-8', errors='replace')


def _read_script_output(execution_id: int, proc: subprocess.Popen, timeout_override=None) -> None:
    """后台线程：逐行读取脚本输出，实时更新到数据库和 state 中

    输出截断策略：保留头部 + 尾部，中间截断
    - 头部保留 max_output 的前 60%
    - 尾部保留 max_output 的后 40%（使用 deque 滑动窗口）
    - 中间用截断标记连接
    这样既能看到脚本启动信息，又能看到最终执行结果
    
    :param timeout_override: 自定义超时时间（秒），None 表示使用默认配置，0 表示不限时长
    """
    from collections import deque
    from flask import current_app
    try:
        max_output = current_app.config.get('EDITOR_MAX_OUTPUT_BYTES', 200 * 1024)
    except RuntimeError:
        max_output = 200 * 1024
    
    # 计算超时时间：优先使用自定义值，0 表示不限时长（设为极大值）
    try:
        default_timeout = current_app.config.get('EDITOR_SCRIPT_TIMEOUT', 300)
    except RuntimeError:
        default_timeout = 300
    
    if timeout_override is not None:
        if timeout_override == 0:
            timeout_seconds = 999999999  # 不限时长（约 31.7 年）
        else:
            timeout_seconds = int(timeout_override)
    else:
        timeout_seconds = default_timeout
    
    try:
        db_flush_interval = current_app.config.get('EDITOR_DB_FLUSH_INTERVAL', 30)
    except RuntimeError:
        db_flush_interval = 30

    head_limit = int(max_output * 0.6)
    tail_limit = int(max_output * 0.4)
    head_buf = io.StringIO()
    tail_lines = deque()
    tail_len = 0
    output_truncated = False
    total_output_len = 0
    timeout_event = threading.Event()

    def _build_display_output():
        if not output_truncated:
            return head_buf.getvalue()
        head_text = head_buf.getvalue()
        tail_text = ''.join(tail_lines)
        truncated_len = total_output_len - len(head_text) - len(tail_text)
        return (
            head_text
            + f'\n...[已截断 {truncated_len} 字节，保留头部和尾部]...\n'
            + tail_text
        )

    def _timeout_monitor():
        if timeout_event.wait(timeout=timeout_seconds):
            return
        logger.warning(f"[FileEditor] 脚本执行超时（{timeout_seconds}秒），强制终止 PID={proc.pid}")
        with _scripts_lock:
            s = _running_scripts.get(execution_id)
            if s:
                s['_timeout_killed'] = True
        kill_process_tree(proc.pid)
        try:
            if proc.stdout and not proc.stdout.closed:
                proc.stdout.close()
        except Exception:
            pass

    timer_thread = threading.Thread(target=_timeout_monitor, daemon=True, name=f'editor-timeout-{execution_id}')
    timer_thread.start()

    last_db_update_time = time.monotonic()
    output_dirty = False

    try:
        for raw_line in proc.stdout:
            decoded = _decode_output(raw_line)
            total_output_len += len(decoded)

            if not output_truncated:
                if head_buf.tell() + len(decoded) <= head_limit:
                    head_buf.write(decoded)
                else:
                    output_truncated = True
                    tail_lines.append(decoded)
                    tail_len += len(decoded)
            else:
                tail_lines.append(decoded)
                tail_len += len(decoded)
                while tail_len > tail_limit and len(tail_lines) > 1:
                    removed = tail_lines.popleft()
                    tail_len -= len(removed)

            output_dirty = True

            with _scripts_lock:
                state = _running_scripts.get(execution_id)
                if state:
                    state['_head_buf'] = head_buf
                    state['_tail_lines'] = tail_lines
                    state['_output_truncated'] = output_truncated
                    state['_total_output_len'] = total_output_len
                    state['_output_dirty'] = True

            now = time.monotonic()
            if now - last_db_update_time >= db_flush_interval:
                display = _build_display_output()
                _update_execution_log(execution_id, display, None)
                last_db_update_time = now
                output_dirty = False

    except Exception as e:
        if not isinstance(e, ValueError):
            err_msg = f'\n[ERROR] 读取输出异常: {e}\n'
            if not output_truncated:
                head_buf.write(err_msg)
            else:
                tail_lines.append(err_msg)

    timeout_event.set()

    try:
        if not proc.stdout.closed:
            proc.stdout.close()
    except Exception:
        pass

    try:
        proc.wait(timeout=3)
    except Exception:
        kill_process_tree(proc.pid)

    output = _build_display_output()
    return_code = proc.returncode
    head_buf.close()

    with _scripts_lock:
        state = _running_scripts.get(execution_id)
        if state:
            state['output'] = output
            for k in ('_head_buf', '_tail_lines', '_output_truncated',
                      '_total_output_len', '_output_dirty'):
                state.pop(k, None)

    stopped = False
    timeout_killed = False
    if state:
        if state.get('_stop_requested'):
            stopped = True
        if state.get('_timeout_killed'):
            timeout_killed = True

    if stopped and not output:
        if state:
            output = state.get('output', '')
        if not output:
            output = '脚本已被用户停止。'

    if not output and not stopped:
        output = '脚本执行完成，无输出。'

    if stopped:
        final_status = 'stopped'
    elif timeout_killed:
        final_status = 'timeout'
    elif return_code == 0:
        final_status = 'success'
    else:
        final_status = 'failed'

    _update_execution_log(execution_id, output, return_code, final_status)

    result = {
        'output': output,
        'returncode': return_code,
        'success': return_code == 0 and not stopped and not timeout_killed,
        'stopped': stopped,
        'timeout': timeout_killed,
    }

    with _scripts_lock:
        state = _running_scripts.get(execution_id)
        if state and state['status'] == 'running':
            state['status'] = 'done'
            state['result'] = result
            state['proc'] = None
            state['done_time'] = time.monotonic()

    def _auto_cleanup(eid, delay=300):
        time.sleep(delay)
        with _scripts_lock:
            s = _running_scripts.get(eid)
            if s and s.get('status') == 'done':
                _running_scripts.pop(eid, None)
                logger.info(f"[FileEditor] 自动清理已完成脚本条目: 执行ID={eid}")
    cleanup_thread = threading.Thread(target=_auto_cleanup, args=(execution_id,), daemon=True)
    cleanup_thread.start()

    logger.info(f"[FileEditor] 脚本执行完成: PID={proc.pid}, 退出码={return_code}, 状态={final_status}")


def _update_execution_log(execution_id: int, log_content: str, return_code=None, status=None) -> None:
    """更新执行日志到数据库"""
    try:
        from app import _app_instance
        if _app_instance is None:
            return
        with _app_instance.app_context():
            log_entry = db.session.get(EditorExecutionLog, execution_id)
            if log_entry:
                if log_content is not None:
                    max_output = current_app.config.get('EDITOR_MAX_OUTPUT_BYTES', 200 * 1024)
                    if len(log_content) > max_output:
                        log_content = log_content[:max_output] + "\n...[日志已截断]"
                    log_entry.log_content = log_content
                if return_code is not None:
                    log_entry.return_code = return_code
                if status is not None:
                    log_entry.status = status
                    if status != 'running':
                        log_entry.end_time = datetime.now()
                        log_entry.pid = None
                db.session.commit()
    except Exception as e:
        logger.error(f"[FileEditor] 更新执行日志异常: {e}")
        try:
            db.session.rollback()
        except Exception:
            pass


@file_editor_bp.route('/api/file/run/result', methods=['GET'])
@login_required
@is_admin_required
def get_run_result() -> Any:
    """轮询获取脚本运行结果（通过 execution_id 查询）"""
    try:
        execution_id = request.args.get('execution_id', type=int)
        if not execution_id:
            return error_response("缺少 execution_id 参数", code=400)
        
        with _scripts_lock:
            state = _running_scripts.get(execution_id)
        
        if state is None:
            log_entry = db.session.get(EditorExecutionLog, execution_id)
            if log_entry:
                return success_response(data={
                    'status': 'done',
                    'output': log_entry.log_content or '',
                    'returncode': log_entry.return_code,
                    'success': log_entry.status == 'success',
                    'stopped': log_entry.status == 'stopped',
                    'timeout': log_entry.status == 'timeout',
                })
            return success_response(data={'status': 'idle'})
        
        if state['status'] == 'running':
            elapsed = time.monotonic() - state.get('start_time', 0)
            # 使用自定义超时时间或默认配置
            timeout_override = state.get('_timeout_override')
            if timeout_override is not None:
                if timeout_override == 0:
                    timeout_seconds = 999999999  # 不限时长
                else:
                    timeout_seconds = int(timeout_override)
            else:
                timeout_seconds = current_app.config.get('EDITOR_SCRIPT_TIMEOUT', 300)
            if elapsed > timeout_seconds:
                proc = state.get('proc')
                if proc:
                    kill_process_tree(proc.pid)
                    try:
                        if proc.stdout and not proc.stdout.closed:
                            proc.stdout.close()
                    except Exception:
                        pass
                return success_response(data={
                    'status': 'running',
                    'output': _build_state_output(state) or '脚本执行超时，正在终止...',
                    'timeout_killing': True,
                })
            
            current_output = _build_state_output(state)
            return success_response(data={
                'status': 'running',
                'output': current_output,
            })
        
        # status == 'done'
        result = state.get('result', {})
        
        return success_response(data={'status': 'done', **result})
    
    except Exception as e:
        logger.error(f"[FileEditor] 获取运行结果异常: {e}", exc_info=True)
        return error_response("获取运行结果失败", code=500)


@file_editor_bp.route('/api/file/stop', methods=['POST'])
@login_required
@is_admin_required
def stop_script() -> Any:
    """停止正在运行的脚本（使用 psutil 终止进程树，与定时任务一致）"""
    try:
        data = request.get_json(silent=True) or {}
        execution_id = data.get('execution_id')
        if not execution_id:
            return error_response("缺少 execution_id 参数", code=400)

        with _scripts_lock:
            state = _running_scripts.get(execution_id)
            if state is None or state.get('status') != 'running':
                return error_response("没有正在运行的脚本", code=400)
            proc = state.get('proc')
            if proc is None:
                return error_response("没有正在运行的脚本", code=400)
            pid = proc.pid
            state['_stop_requested'] = True

        kill_process_tree(pid)

        with _scripts_lock:
            state = _running_scripts.get(execution_id)
            if state:
                proc = state.get('proc')
                if proc:
                    try:
                        if proc.stdout and not proc.stdout.closed:
                            proc.stdout.close()
                    except Exception:
                        pass
                    try:
                        if proc.stderr and not proc.stderr.closed:
                            proc.stderr.close()
                    except Exception:
                        pass

        logger.info(f"[FileEditor] 用户 {current_user.id} 停止了脚本 (PID: {pid}, 执行ID: {execution_id})")
        return success_response("脚本已停止")

    except Exception as e:
        logger.error(f"[FileEditor] 停止脚本异常: {e}", exc_info=True)
        return error_response("停止脚本失败", code=500)


@file_editor_bp.route('/api/browse/folders', methods=['GET'])
@login_required
@is_admin_required
def browse_folders() -> Any:
    """
    浏览文件夹（用于项目路径选择）
    参数：path (可选，默认为系统根目录)
    """
    try:
        path = request.args.get('path', '')
        
        # 如果没有指定路径，返回系统常见路径
        if not path:
            # Windows系统
            if sys.platform == 'win32':
                common_paths = {
                    'current_dir': os.getcwd(),
                    'home_dir': os.path.expanduser('~'),
                    'desktop': os.path.join(os.path.expanduser('~'), 'Desktop'),
                    'documents': os.path.join(os.path.expanduser('~'), 'Documents'),
                    'downloads': os.path.join(os.path.expanduser('~'), 'Downloads'),
                    'c_drive': 'C:\\',
                    'd_drive': 'D:\\' if os.path.exists('D:\\') else None,
                    'e_drive': 'E:\\' if os.path.exists('E:\\') else None,
                }
            else:
                # Linux/Mac系统
                common_paths = {
                    'current_dir': os.getcwd(),
                    'home_dir': os.path.expanduser('~'),
                    'desktop': os.path.join(os.path.expanduser('~'), 'Desktop'),
                    'documents': os.path.join(os.path.expanduser('~'), 'Documents'),
                    'downloads': os.path.join(os.path.expanduser('~'), 'Downloads'),
                    'root': '/',
                }
            
            # 过滤None值
            common_paths = {k: v for k, v in common_paths.items() if v is not None}
            
            return success_response(data={
                'type': 'quick_paths',
                'paths': common_paths
            })
        
        # 规范化路径
        path = os.path.abspath(path)
        
        # 安全检查
        allowed = False
        for root_dir in ALLOWED_ROOT_DIRS:
            norm_root_dir = os.path.normpath(root_dir)
            if os.path.normpath(path).lower().startswith(norm_root_dir.lower()):
                allowed = True
                break
        
        if not allowed:
            logger.warning(f"[FileEditor] 路径安全检查失败: {path}")
            return error_response("该路径不在允许访问的范围内", code=403)
        
        if not os.path.isdir(path):
            return error_response("路径不存在或不是文件夹", code=400)
        
        # 获取子文件夹列表
        folders = []
        
        def _has_subfolders(folder_path: str) -> bool:
            """安全地检查文件夹是否包含子目录，权限受限时返回False"""
            try:
                for e in os.listdir(folder_path):
                    if not e.startswith('.') and os.path.isdir(os.path.join(folder_path, e)):
                        return True
                return False
            except (PermissionError, OSError):
                return False
        
        try:
            for entry in sorted(os.listdir(path)):
                full_path = os.path.join(path, entry)
                if os.path.isdir(full_path) and not entry.startswith('.'):
                    folders.append({
                        'name': entry,
                        'path': full_path,
                        'has_subfolders': _has_subfolders(full_path)
                    })
        except PermissionError:
            return error_response("没有权限访问此文件夹", code=403)
        
        return success_response(data={
            'type': 'folder_list',
            'current_path': path,
            'parent_path': os.path.dirname(path) if path != os.path.dirname(path) else None,
            'folders': folders
        })
    
    except Exception as e:
        logger.error(f"[FileEditor] 浏览文件夹异常: {e}", exc_info=True)
        return error_response("浏览文件夹失败", code=500)


@file_editor_bp.route('/api/browse/files', methods=['GET'])
@login_required
@is_admin_required
def browse_files() -> Any:
    """
    浏览文件（用于Python可执行文件选择）
    参数：path (可选，默认为系统根目录)
    """
    try:
        path = request.args.get('path', '')
        file_types = request.args.get('types', '.exe,.bat,.cmd,.sh,.py,.python')
        
        # 如果没有指定路径，返回常见Python安装路径
        if not path:
            common_paths = {}
            
            if sys.platform == 'win32':
                # Windows常见Python安装路径
                common_paths = {
                    'current_dir': os.getcwd(),
                    'home_dir': os.path.expanduser('~'),
                    'program_files': 'C:\\Program Files',
                    'program_files_x86': 'C:\\Program Files (x86)',
                    'local_appdata': os.path.join(os.path.expanduser('~'), 'AppData', 'Local'),
                    'c_drive': 'C:\\',
                    'd_drive': 'D:\\' if os.path.exists('D:\\') else None,
                }
                
                # 查找常见的Python安装位置
                python_locations = [
                    os.path.join(os.path.expanduser('~'), 'AppData', 'Local', 'Programs', 'Python'),
                    'C:\\Python39',
                    'C:\\Python38',
                    'C:\\Python310',
                    'C:\\Python311',
                    'C:\\Program Files\\Python39',
                    'C:\\Program Files\\Python38',
                    'C:\\Program Files\\Python310',
                    'C:\\Program Files\\Python311',
                ]
                
                for loc in python_locations:
                    if os.path.exists(loc):
                        common_paths[f'python_{os.path.basename(loc)}'] = loc
            else:
                # Linux/Mac常见Python路径
                common_paths = {
                    'current_dir': os.getcwd(),
                    'home_dir': os.path.expanduser('~'),
                    'usr_bin': '/usr/bin',
                    'usr_local_bin': '/usr/local/bin',
                    'opt': '/opt',
                    'root': '/',
                }
            
            # 过滤None值
            common_paths = {k: v for k, v in common_paths.items() if v is not None}
            
            return success_response(data={
                'type': 'quick_paths',
                'paths': common_paths
            })
        
        # 规范化路径
        path = os.path.abspath(path)
        
        # 安全检查
        allowed = False
        for root_dir in ALLOWED_ROOT_DIRS:
            norm_root_dir = os.path.normpath(root_dir)
            if os.path.normpath(path).lower().startswith(norm_root_dir.lower()):
                allowed = True
                break
        
        if not allowed:
            logger.warning(f"[FileEditor] 路径安全检查失败: {path}")
            return error_response("该路径不在允许访问的范围内", code=403)
        
        if not os.path.isdir(path):
            return error_response("路径不存在或不是文件夹", code=400)
        
        # 获取文件列表
        files = []
        folders = []
        
        try:
            for entry in sorted(os.listdir(path)):
                if entry.startswith('.'):
                    continue
                    
                full_path = os.path.join(path, entry)
                
                if os.path.isdir(full_path):
                    folders.append({
                        'name': entry,
                        'path': full_path,
                    })
                else:
                    # 检查文件扩展名
                    ext = os.path.splitext(entry)[1].lower()
                    if ext in file_types.split(','):
                        files.append({
                            'name': entry,
                            'path': full_path,
                            'size': os.path.getsize(full_path)
                        })
        except PermissionError:
            return error_response("没有权限访问此文件夹", code=403)
        
        return success_response(data={
            'type': 'file_list',
            'current_path': path,
            'parent_path': os.path.dirname(path) if path != os.path.dirname(path) else None,
            'folders': folders,
            'files': files
        })
    
    except Exception as e:
        logger.error(f"[FileEditor] 浏览文件异常: {e}", exc_info=True)
        return error_response("浏览文件失败", code=500)


# ============================================================
# 执行记录管理接口
# ============================================================

@file_editor_bp.route('/api/executions', methods=['GET'])
@login_required
@is_admin_required
def get_execution_list() -> Any:
    """查询用户的脚本执行记录列表（保存1天）"""
    try:
        user_id = current_user.id
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        status = request.args.get('status', '')
        
        query = EditorExecutionLog.query.filter(
            EditorExecutionLog.user_id == user_id
        )
        
        if status:
            query = query.filter(EditorExecutionLog.status == status)
        
        query = query.order_by(EditorExecutionLog.start_time.desc())
        
        total = query.count()
        offset = (page - 1) * page_size
        records = query.offset(offset).limit(page_size).all()
        
        result = [{
            'id': r.id,
            'script_path': r.script_path,
            'python_path': r.python_path,
            'status': r.status,
            'pid': r.pid,
            'return_code': r.return_code,
            'start_time': r.start_time.isoformat() if r.start_time else None,
            'end_time': r.end_time.isoformat() if r.end_time else None,
        } for r in records]
        
        return success_response(data={
            'list': result,
            'total': total,
            'page': page,
            'page_size': page_size
        })
    
    except Exception as e:
        logger.error(f"[FileEditor] 查询执行记录异常: {e}", exc_info=True)
        return error_response("查询执行记录失败", code=500)


@file_editor_bp.route('/api/executions/<int:execution_id>', methods=['GET'])
@login_required
@is_admin_required
def get_execution_detail(execution_id: int) -> Any:
    """获取单个执行记录详情"""
    try:
        user_id = current_user.id
        record = EditorExecutionLog.query.filter(
            EditorExecutionLog.id == execution_id,
            EditorExecutionLog.user_id == user_id
        ).first()
        
        if not record:
            return error_response("执行记录不存在", code=404)
        
        return success_response(data={
            'id': record.id,
            'script_path': record.script_path,
            'python_path': record.python_path,
            'status': record.status,
            'log_content': record.log_content,
            'pid': record.pid,
            'return_code': record.return_code,
            'start_time': record.start_time.isoformat() if record.start_time else None,
            'end_time': record.end_time.isoformat() if record.end_time else None,
        })
    
    except Exception as e:
        logger.error(f"[FileEditor] 获取执行记录详情异常: {e}", exc_info=True)
        return error_response("获取执行记录详情失败", code=500)


@file_editor_bp.route('/api/executions/export', methods=['GET'])
@login_required
@is_admin_required
def export_executions() -> Any:
    """导出执行记录（CSV格式）"""
    try:
        from flask import make_response
        import csv
        from io import StringIO
        
        user_id = current_user.id
        status = request.args.get('status', '')
        
        query = EditorExecutionLog.query.filter(
            EditorExecutionLog.user_id == user_id
        )
        
        if status:
            query = query.filter(EditorExecutionLog.status == status)
        
        records = query.order_by(EditorExecutionLog.start_time.desc()).limit(500).all()
        
        output = StringIO()
        writer = csv.writer(output)
        
        writer.writerow([
            'ID', '脚本路径', 'Python路径', '状态', '退出码', 
            '进程ID', '开始时间', '结束时间', '日志内容'
        ])
        
        for record in records:
            writer.writerow([
                record.id,
                record.script_path,
                record.python_path,
                record.status,
                record.return_code,
                record.pid,
                record.start_time.strftime('%Y-%m-%d %H:%M:%S') if record.start_time else '',
                record.end_time.strftime('%Y-%m-%d %H:%M:%S') if record.end_time else '',
                record.log_content[:500] if record.log_content else ''  # 日志内容截取前500字符
            ])
        
        output.seek(0)
        response = make_response(output.getvalue())
        response.headers['Content-Type'] = 'text/csv; charset=utf-8'
        response.headers['Content-Disposition'] = 'attachment; filename=executions.csv'
        
        return response
    
    except Exception as e:
        logger.error(f"[FileEditor] 导出执行记录异常: {e}", exc_info=True)
        return error_response("导出执行记录失败", code=500)


@file_editor_bp.route('/api/executions/<int:execution_id>', methods=['DELETE'])
@login_required
@is_admin_required
def delete_execution(execution_id: int) -> Any:
    """删除单个执行记录"""
    try:
        user_id = current_user.id
        record = EditorExecutionLog.query.filter(
            EditorExecutionLog.id == execution_id,
            EditorExecutionLog.user_id == user_id
        ).first()
        
        if not record:
            return error_response("执行记录不存在", code=404)
        
        db.session.delete(record)
        db.session.commit()
        
        return success_response("删除成功")
    
    except Exception as e:
        logger.error(f"[FileEditor] 删除执行记录异常: {e}", exc_info=True)
        return error_response("删除执行记录失败", code=500)


def cleanup_old_execution_logs() -> None:
    """清理超过1天的执行日志"""
    from datetime import timedelta
    from app import _app_instance

    if _app_instance is None:
        return

    with _app_instance.app_context():
        try:
            cutoff_time = datetime.now() - timedelta(days=1)
            deleted_count = EditorExecutionLog.query.filter(
                EditorExecutionLog.start_time < cutoff_time
            ).delete(synchronize_session=False)

            if deleted_count > 0:
                db.session.commit()
                logger.info(f"[FileEditor] 清理过期执行日志 {deleted_count} 条")
        except Exception as e:
            logger.error(f"[FileEditor] 清理过期执行日志异常: {e}", exc_info=True)
            try:
                db.session.rollback()
            except Exception:
                pass