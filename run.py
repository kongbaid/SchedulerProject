"""
Flask 脚本定时任务管理系统 - 启动入口
用法：
    python run.py                      # 开发模式
    python run.py --host 0.0.0.0       # 监听所有网络接口
    gunicorn -w 4 -b 0.0.0.0:5000 run:app  # 生产部署（Gunicorn）
"""
import argparse
import sys
import os

# 将项目根目录加入 Python 路径
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app import create_app

# 创建 Flask 应用实例（供 Gunicorn 使用）
app = create_app()


def main() -> None:
    """启动开发服务器"""
    parser = argparse.ArgumentParser(description="脚本任务管理系统")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8082, help="监听端口")
    parser.add_argument("--debug", action="store_true", default=False, help="调试模式")
    args = parser.parse_args()

    print("=" * 60)
    print("  脚本定时任务管理系统")
    print(f"  访问地址: http://{args.host}:{args.port}")
    print(f"  默认账号: admin / admin123")
    print("=" * 60)

    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        # 禁用 Flask 自带的 reloader（避免 APScheduler 重复启动）
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
