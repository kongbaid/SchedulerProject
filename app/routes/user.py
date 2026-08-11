"""
用户管理路由
处理用户的增删改查、密码重置、任务权限分配
"""
import logging
from typing import Any

from flask import Blueprint, request, render_template
from flask_login import login_required, current_user

from app.extensions import db
from app.models.user import User, ROLE_ADMIN, ROLE_USER
from app.models.task import ScriptTask
from app.utils.response import success_response, error_response

logger = logging.getLogger(__name__)

user_bp = Blueprint("user", __name__, template_folder="../templates/users")


def admin_required(f):
    """管理员权限检查装饰器"""
    from functools import wraps
    from flask_login import current_user
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.role != ROLE_ADMIN:
            return error_response("需要管理员权限", code=403)
        return f(*args, **kwargs)
    
    return decorated_function


# ============================================================
# 页面路由
# ============================================================

@user_bp.route("/users")
@login_required
@admin_required
def user_list_page() -> str:
    """用户管理页面"""
    return render_template("users/list.html")



@user_bp.route("/api/users", methods=["GET"])
@login_required
@admin_required
def user_list_api() -> Any:
    """
    获取用户列表（仅管理员）
    GET 参数：page, per_page, keyword
    """

    try:
        page: int = request.args.get("page", 1, type=int)
        per_page: int = request.args.get("per_page", 10, type=int)
        keyword: str = request.args.get("keyword", "", type=str).strip()

        # 参数安全边界
        page = max(1, page)
        per_page = min(max(1, per_page), 100)

        query = User.query
        if keyword:
            query = query.filter(User.username.like(f"%{keyword}%"))

        query = query.order_by(User.created_at.desc())
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)

        items = [user.to_dict(include_tasks=False) for user in pagination.items]

        return success_response(data={
            "items": items,
            "total": pagination.total,
            "page": pagination.page,
            "per_page": pagination.per_page,
            "pages": pagination.pages,
        })

    except Exception as e:
        logger.error(f"[User] 获取用户列表异常: {e}", exc_info=True)
        return error_response("获取用户列表失败", code=500)


@user_bp.route("/api/user", methods=["POST"])
@login_required
@admin_required
def add_user_api() -> Any:
    """
    添加用户（仅管理员）
    请求体 JSON：username, password, role
    """

    try:
        data = request.get_json(silent=True) or {}
        username: str = (data.get("username") or "").strip()
        password: str = (data.get("password") or "").strip()
        role: str = data.get("role", ROLE_USER)
        can_create_task: bool = data.get("can_create_task", False)

        # 参数校验
        if not username or not password:
            return error_response("用户名和密码不能为空", code=400)

        if len(username) > 80 or len(username) < 3:
            return error_response("用户名长度需在 3-80 字符之间", code=400)

        if len(password) < 6:
            return error_response("密码长度至少 6 个字符", code=400)

        if role not in [ROLE_ADMIN, ROLE_USER]:
            return error_response("角色必须是 admin 或 user", code=400)

        # 检查用户名是否已存在
        existing = User.query.filter_by(username=username).first()
        if existing:
            return error_response(f"用户名 '{username}' 已存在", code=400)

        # 创建用户
        user = User(
            username=username,
            role=role,
            is_active_user=True,
            can_create_task=can_create_task
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()

        logger.info(f"[User] 管理员 '{current_user.username}' 创建用户: '{username}' (ID={user.id})")
        return success_response("用户创建成功", data={"user": user.to_dict()})

    except Exception as e:
        logger.error(f"[User] 创建用户异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("创建用户失败", code=500)


@user_bp.route("/api/user/<int:user_id>", methods=["PUT"])
@login_required
@admin_required
def update_user_api(user_id: int) -> Any:
    """
    更新用户信息（仅管理员）
    请求体 JSON：username, role, is_active
    """

    try:
        user = db.session.get(User, user_id)
        if not user:
            return error_response("用户不存在", code=400)

        # 不能修改管理员用户的关键信息
        if user.role == ROLE_ADMIN and current_user.role != ROLE_ADMIN:
            return error_response("不能修改管理员账号", code=403)

        data = request.get_json(silent=True) or {}
        username = data.get("username")
        role = data.get("role")
        is_active = data.get("is_active")
        can_create_task = data.get("can_create_task")

        # 更新用户名
        if username:
            username = username.strip()
            if len(username) < 3 or len(username) > 80:
                return error_response("用户名长度需在 3-80 字符之间", code=400)

            # 检查用户名是否已被其他用户使用
            existing = User.query.filter_by(username=username).first()
            if existing and existing.id != user_id:
                return error_response(f"用户名 '{username}' 已存在", code=400)

            user.username = username

        # 更新角色
        if role:
            if role not in [ROLE_ADMIN, ROLE_USER]:
                return error_response("角色必须是 admin 或 user", code=400)
            user.role = role

        # 更新激活状态
        if is_active is not None:
            user.is_active_user = bool(is_active)
        
        # 更新创建任务权限
        if can_create_task is not None:
            user.can_create_task = bool(can_create_task)

        db.session.commit()

        logger.info(f"[User] 管理员 '{current_user.username}' 更新用户: '{user.username}' (ID={user_id})")
        return success_response("用户更新成功", data={"user": user.to_dict()})

    except Exception as e:
        logger.error(f"[User] 更新用户异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("更新用户失败", code=500)


@user_bp.route("/api/user/<int:user_id>/password", methods=["PUT"])
@login_required
@admin_required
def reset_password_api(user_id: int) -> Any:
    """
    重置用户密码（仅管理员）
    请求体 JSON：new_password
    """

    try:
        user = db.session.get(User, user_id)
        if not user:
            return error_response("用户不存在", code=400)

        data = request.get_json(silent=True) or {}
        new_password: str = (data.get("new_password") or "").strip()

        if not new_password:
            return error_response("新密码不能为空", code=400)

        if len(new_password) < 6:
            return error_response("密码长度至少 6 个字符", code=400)

        # 重置密码
        user.set_password(new_password)
        db.session.commit()

        logger.info(f"[User] 管理员 '{current_user.username}' 重置用户密码: '{user.username}' (ID={user_id})")
        return success_response("密码重置成功")

    except Exception as e:
        logger.error(f"[User] 重置密码异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("重置密码失败", code=500)


@user_bp.route("/api/user/<int:user_id>", methods=["DELETE"])
@login_required
@admin_required
def delete_user_api(user_id: int) -> Any:
    """
    删除用户（仅管理员）
    """

    try:
        user = db.session.get(User, user_id)
        if not user:
            return error_response("用户不存在", code=400)

        # 不能删除管理员用户
        if user.role == ROLE_ADMIN:
            return error_response("不能删除管理员账号", code=403)

        username = user.username
        db.session.delete(user)
        db.session.commit()

        logger.info(f"[User] 管理员 '{current_user.username}' 删除用户: '{username}' (ID={user_id})")
        return success_response("用户已删除")

    except Exception as e:
        logger.error(f"[User] 删除用户异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("删除用户失败", code=500)


@user_bp.route("/api/user/<int:user_id>/tasks", methods=["GET"])
@login_required
@admin_required
def get_user_tasks_api(user_id: int) -> Any:
    """
    获取用户可管理的任务列表
    """

    try:
        user = db.session.get(User, user_id)
        if not user:
            return error_response("用户不存在", code=400)

        # 获取用户可管理的任务（使用 JOIN 优化查询）
        from app.models.permission import UserTaskPermission
        permissions = UserTaskPermission.query.filter_by(user_id=user_id).all()
        task_list = []
        for perm in permissions:
            task = perm.task
            if task:
                task_list.append({
                    "id": task.id,
                    "task_name": task.task_name,
                    "script_path": task.script_path,
                    "last_status": task.last_status,
                    "can_edit_script": perm.can_edit_script
                })

        return success_response(data={"tasks": task_list})

    except Exception as e:
        logger.error(f"[User] 获取用户任务异常: {e}", exc_info=True)
        return error_response("获取用户任务失败", code=500)


@user_bp.route("/api/user/<int:user_id>/tasks", methods=["POST"])
@login_required
@admin_required
def assign_task_api(user_id: int) -> Any:
    """
    为用户分配任务（仅管理员）
    请求体 JSON：task_id, can_edit_script（可选，默认True）
    """

    try:
        from app.models.permission import UserTaskPermission
        user = db.session.get(User, user_id)
        if not user:
            return error_response("用户不存在", code=400)

        data = request.get_json(silent=True) or {}
        task_id = data.get("task_id")
        can_edit_script = data.get("can_edit_script", True)

        if not task_id:
            return error_response("任务 ID 不能为空", code=400)

        # 检查任务是否存在
        task = db.session.get(ScriptTask, task_id)
        if not task:
            return error_response("任务不存在", code=400)

        # 检查是否已分配
        existing_perm = UserTaskPermission.query.filter_by(
            user_id=user_id,
            task_id=task_id
        ).first()
        if existing_perm:
            return error_response("该任务已分配给此用户", code=400)

        # 分配任务
        permission = UserTaskPermission(
            user_id=user_id,
            task_id=task_id,
            can_edit_script=can_edit_script
        )
        db.session.add(permission)
        db.session.commit()

        logger.info(
            f"[User] 管理员 '{current_user.username}' 为用户 '{user.username}' "
            f"分配任务 '{task.task_name}' (ID={task_id}, can_edit_script={can_edit_script})"
        )
        return success_response("任务分配成功")

    except Exception as e:
        logger.error(f"[User] 分配任务异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("分配任务失败", code=500)


@user_bp.route("/api/user/<int:user_id>/tasks/<int:task_id>", methods=["DELETE"])
@login_required
@admin_required
def remove_task_api(user_id: int, task_id: int) -> Any:
    """
    移除用户的任务权限（仅管理员）
    """

    try:
        from app.models.permission import UserTaskPermission
        user = db.session.get(User, user_id)
        if not user:
            return error_response("用户不存在", code=400)

        task = db.session.get(ScriptTask, task_id)
        if not task:
            return error_response("任务不存在", code=400)

        # 检查是否已分配
        existing_perm = UserTaskPermission.query.filter_by(
            user_id=user_id,
            task_id=task_id
        ).first()
        if not existing_perm:
            return error_response("该任务未分配给此用户", code=400)

        # 移除任务权限
        db.session.delete(existing_perm)
        db.session.commit()

        logger.info(
            f"[User] 管理员 '{current_user.username}' 移除用户 '{user.username}' "
            f"的任务 '{task.task_name}' (ID={task_id})"
        )
        return success_response("任务权限已移除")

    except Exception as e:
        logger.error(f"[User] 移除任务异常: {e}", exc_info=True)
        db.session.rollback()
        return error_response("移除任务失败", code=500)


@user_bp.route("/api/user/current", methods=["GET"])
@login_required
def get_current_user_api() -> Any:
    """
    获取当前登录用户信息
    """
    return success_response(data={"user": current_user.to_dict()})
