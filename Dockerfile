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

# 数据持久化目录（fly.io 使用 volumes）
RUN mkdir -p /app/data /app/reports

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/api/stats', timeout=3)" || exit 1

EXPOSE 8001

CMD ["python", "server.py"]
