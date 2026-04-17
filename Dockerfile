FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY server.py .
COPY templates/ ./templates/

# 创建输出和 Session 目录
RUN mkdir -p /app/output /app/sessions

# 环境变量
ENV OUTPUT_DIR=/app/output

# 暴露端口
EXPOSE 7860

# 启动应用
CMD ["python", "server.py"]
