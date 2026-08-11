"""
Flask 扩展实例模块
集中管理所有 Flask 扩展的实例，避免循环导入
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

# SQLAlchemy ORM 实例
db = SQLAlchemy()

# 登录管理器
login_manager = LoginManager()
login_manager.login_view = "auth.login_page"  # 未登录时重定向的路由
login_manager.login_message = "请先登录后再访问该页面"
login_manager.login_message_category = "warning"
