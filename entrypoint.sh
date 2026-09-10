#!/bin/bash
# jeeseek.top 入口脚本
# 功能：
#   1. 加载 Render Secret Files (/etc/secrets/*.env) 到环境变量
#   2. 启动 server.py

set -e

SECRET_DIR="/etc/secrets"

echo "[entrypoint] 启动 jeeseek 服务..."

# 加载 Render Secret Files
if [ -d "$SECRET_DIR" ]; then
    for f in "$SECRET_DIR"/*.env "$SECRET_DIR"/.env; do
        if [ -f "$f" ]; then
            echo "[entrypoint] 加载 Secret File: $f"
            # 逐行加载，忽略空行和注释
            while IFS= read -r line || [ -n "$line" ]; do
                # 跳过空行和注释
                [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
                # 导出环境变量
                if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
                    key="${BASH_REMATCH[1]}"
                    value="${BASH_REMATCH[2]}"
                    # 去掉前后空格和单引号/双引号
                    value=$(echo "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e "s/^['"]//" -e "s/['"]$//")
                    export "$key"="$value"
                fi
            done < "$f"
        fi
    done
else
    echo "[entrypoint] 警告: $SECRET_DIR 不存在，Secret Files 未加载"
fi

# 验证关键环境变量
echo "[entrypoint] 环境变量检查:"
[ -n "$AGNES_API_KEY" ] && echo "  ✓ AGNES_API_KEY 已加载 (长度: ${#AGNES_API_KEY})" || echo "  ✗ AGNES_API_KEY 缺失"
[ -n "$TENCENT_TMT_SECRET_ID" ] && echo "  ✓ TENCENT_TMT_SECRET_ID 已加载" || echo "  ✗ TENCENT_TMT_SECRET_ID 缺失"

# 启动 server
echo "[entrypoint] 启动 server.py..."
exec python server.py
