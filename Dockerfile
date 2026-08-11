FROM python:3.11-slim

# 无缓冲输出，便于容器日志实时查看
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# 仅安装时区数据（APScheduler 依赖系统时区）
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先装依赖，利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目源码
# 注意：config.py 不进入镜像（见 .dockerignore），由下面的模板生成
COPY . .

# 由脱敏模板生成容器配置；运行时通过环境变量覆盖数据库等敏感项
RUN cp config_example.py config.py

EXPOSE 8082

# 单进程启动：APScheduler 自带文件锁，多 worker 下由 master 进程持有调度器
# 如需 Gunicorn，可用：gunicorn -w 1 -b 0.0.0.0:8082 run:app
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8082"]
