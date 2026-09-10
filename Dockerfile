FROM python:3.11-slim

WORKDIR /app

# 系统依赖（feedparser + sqlite + 时区）
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    && ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 代码
COPY . .

# 数据持久化目录（fly.io 使用 volumes / Zeabur 临时盘）
# 同时创建 server.py 需要的日志目录
RUN mkdir -p /app/data /app/reports /var/log/jeeseek /var/data /var/data/reports && \
    chmod 777 /var/log/jeeseek

# 入口脚本：先加载 Render Secret Files (/etc/secrets/*.env) 再启动 server
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# 默认端口 — Zeabur 默认 PORT=8080，fly.io 用 LISTEN_PORT=8001
# server.py 优先读 PORT，fallback LISTEN_PORT
ENV LISTEN_PORT=8080
ENV LISTEN_HOST=0.0.0.0
EXPOSE 8080

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/stats', timeout=3)" || exit 1

CMD ["/entrypoint.sh"]
