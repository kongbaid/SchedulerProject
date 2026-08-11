"""
认证路由
处理登录、登出、会话管理
"""
import logging
import time
import threading
from typing import Any

from flask import Blueprint, render_template, request, redirect, url_for
from flask_login import login_user, logout_user, login_required, current_user

from app.extensions import db
from app.models.user import User
from app.utils.response import success_response, error_response

logger = logging.getLogger(__name__)

# ============================================================
# 登录频率限制（内存级，防止暴力破解）
# ============================================================
_login_attempts: dict = {}   # {ip: [{'time': ts, 'count': n}]}
_login_lock = threading.Lock()
_LOGIN_MAX_ATTEMPTS = 10     # 最多 10 次尝试
_LOGIN_WINDOW_SECONDS = 300  # 在 5 分钟内

auth_bp = Blueprint(
    "auth", __name__,
    template_folder="../templates/auth",
)


@auth_bp.route("/login", methods=["GET"])
def login_page() -> str:
    """登录页面（渲染模板）"""
    if current_user.is_authenticated:
        return redirect(url_for("task.task_list_page"))
    return render_template("auth/login.html")


def _check_login_rate_limit(ip: str) -> bool:
    """
    检查 IP 是否超过登录频率限制
    :return: True=允许登录, False=已被限制
    """
    now = time.monotonic()
    with _login_lock:
        attempts = _login_attempts.get(ip, [])
        # 清理过期记录
        attempts = [a for a in attempts if now - a < _LOGIN_WINDOW_SECONDS]
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            _login_attempts[ip] = attempts
            return False
        attempts.append(now)
        _login_attempts[ip] = attempts
        return True


@auth_bp.route("/api/login", methods=["POST"])
def login_api() -> Any:
    """
    登录接口（AJAX）
    请求参数：username, password
    """
    try:
        # 登录频率限制
        client_ip = request.remote_addr or 'unknown'
        if not _check_login_rate_limit(client_ip):
            logger.warning(f"[Auth] 登录频率限制触发: IP={client_ip}")
            return error_response("登录尝试过于频繁，请 5 分钟后再试", code=429)

        data = request.get_json(silent=True) or {}
        username: str = (data.get("username") or "").strip()
        password: str = (data.get("password") or "").strip()

        # 参数校验
        if not username or not password:
            return error_response("用户名和密码不能为空", code=400)

        # 防止注入：限制长度
        if len(username) > 80 or len(password) > 128:
            return error_response("参数长度超限", code=400)

        # 查询用户
        user = User.query.filter_by(username=username).first()
        if not user or not user.check_password(password):
            return error_response("用户名或密码错误", code=400)

        if not user.is_active_user:
            return error_response("账号已被禁用", code=400)

        # 登录成功，清除限频记录
        with _login_lock:
            _login_attempts.pop(client_ip, None)

        login_user(user, remember=True)
        logger.info(f"[Auth] 用户 '{username}' 登录成功")
        return success_response("登录成功", data={"redirect": url_for("task.task_list_page")})

    except Exception as e:
        logger.error(f"[Auth] 登录异常: {e}", exc_info=True)
        return error_response("服务器异常", code=500)


@auth_bp.route("/logout", methods=["GET", "POST"])
@login_required
def logout() -> Any:
    """登出"""
    logout_user()
    return redirect(url_for("auth.login_page"))
