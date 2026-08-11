# 部署说明

## 服务器部署步骤

### 1. 环境准备

```bash
# 安装 Python 3.7+
python --version

# 安装 MySQL 8.0+
mysql --version
```

### 2. 初始化数据库

```bash
# 登录 MySQL
mysql -u root -p

# 执行初始化脚本
source init_db.sql
```

### 3. 部署项目

```bash
# 上传项目文件到服务器（排除 venv、__pycache__、.idea 等）

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或
venv\Scripts\activate     # Windows

# 安装依赖
pip install -r requirements.txt

# 修改配置文件
vi config.py
# 修改 MySQL 连接信息、SECRET_KEY 等
```

### 4. 启动应用

```bash
# 开发环境
python run.py

# 生产环境（推荐使用 gunicorn）
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 run:app
```

### 5. 配置 Nginx（可选）

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /static {
        alias /path/to/your/project/static;
    }
}
```

## 配置说明

### config.py 关键配置

```python
# 数据库配置（生产环境必须使用 MySQL）
SQLALCHEMY_DATABASE_URI = 'mysql+pymysql://user:password@host:3306/task_manager'

# 密钥（生产环境必须修改）
SECRET_KEY = 'your-secret-key-here'

# 日志清理（保留天数）
LOG_CLEAN_DAYS = 30
```

## 注意事项

1. **生产环境必须使用 MySQL**，不支持 SQLite
2. **必须修改 SECRET_KEY**，使用强随机字符串
3. **定期备份数据库**
4. **配置防火墙**，仅开放必要端口
5. **使用 HTTPS**（生产环境推荐）

## 默认账号

- 用户名：admin
- 密码：admin123（首次登录后立即修改）

## 常见问题

### Q: 调度器未启动？
A: 检查文件锁 `.scheduler.lock`，确保只有一个进程运行

### Q: 任务执行失败？
A: 检查脚本路径是否正确，Python 路径是否有效

### Q: 日志占用空间过大？
A: 系统会自动清理 30 天前的日志，也可手动清理
