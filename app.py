"""
Flask 脚本定时任务管理系统 - 兼容入口
推荐使用 run.py 启动，此文件保留兼容性
"""
from run import app  # noqa: F401

if __name__ == "__main__":
    from run import main
    main()
