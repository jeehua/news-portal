#!/bin/bash
# jeeseek.top 入口脚本
# 功能：
#   1. 加载 Render Secret Files (/etc/secrets/*.env) 到环境变量
#   2. 启动 server.py

# v5.2.1 debug: 不再 set -e（让 server 启动失败时 entrypoint 继续记录 log）
# 反而：让 server.py 内部用 try/except 处理致命错误，避免 container 崩溃
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
                    # 去掉前后空格
                    value="${value#"${value%%[![:space:]]*}"}"
                    value="${value%"${value##*[![:space:]]}"}"
                    # 去掉首尾单/双引号（用 bash 内置，避免 sed 单引号转义陷阱）
                    if [[ "$value" == \"*\" ]]; then
                        value="${value:1:-1}"
                    elif [[ "$value" == \'*\' ]]; then
                        value="${value:1:-1}"
                    fi
                    export "$key"="$value"
                fi
            done < "$f"
        fi
    done
else
    echo "[entrypoint] 警告: $SECRET_DIR 不存在，Secret Files 未加载"
fi

# v5.2.1 debug: 全面环境变量体检
echo "[entrypoint] === v5.2.1 debug 环境变量检查 ==="
[ -n "$AGNES_API_KEY" ] && echo "  ✓ AGNES_API_KEY (${#AGNES_API_KEY} chars)" || echo "  ✗ AGNES_API_KEY MISSING"
[ -n "$TENCENT_TMT_SECRET_ID" ] && echo "  ✓ TENCENT_TMT_SECRET_ID" || echo "  ✗ TENCENT_TMT_SECRET_ID MISSING"
[ -n "$DATABASE_URL" ] && echo "  ✓ DATABASE_URL (${#DATABASE_URL} chars, prefix: ${DATABASE_URL:0:30}...)" || echo "  - DATABASE_URL 未设置 (走 SQLite 模式)"
echo "[entrypoint] ==="

# v5.2.1 debug: 探针 — 如果有 DATABASE_URL，测试 psycopg2 import 和 connect
if [ -n "$DATABASE_URL" ]; then
    echo "[entrypoint] DATABASE_URL 存在，测试 psycopg2 + connect..."
    python -c "
import sys
try:
    import psycopg2
    print('[probe] psycopg2 import OK, version:', psycopg2.__version__)
except Exception as e:
    print('[probe] psycopg2 import FAIL:', repr(e))
    sys.exit(0)  # 不让探针失败
try:
    conn = psycopg2.connect('$DATABASE_URL', connect_timeout=5)
    cur = conn.cursor()
    cur.execute('SELECT current_database(), current_user, version()')
    db, user, ver = cur.fetchone()
    print(f'[probe] connect OK: db={db} user={user}')
    cur.execute(\"SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name\")
    tables = [r[0] for r in cur.fetchall()]
    print(f'[probe] existing tables: {tables}')
    conn.close()
except Exception as e:
    print('[probe] connect FAIL:', repr(e))
" 2>&1
    echo "[entrypoint] 探针完成 (无论是否成功，server 都要启动)"
fi

# v5.2.1 debug: 测试 server.py 模块能否 import
echo "[entrypoint] === 探针: server.py import 测试 ==="
python -c "
import sys
import traceback
try:
    import server
    print('[probe] server.py import OK')
    print('[probe] USE_POSTGRES:', server.db.USE_POSTGRES if hasattr(server, 'db') else 'unknown')
except Exception as e:
    print('[probe] server.py import FAIL:', repr(e))
    print('[probe] traceback:')
    print(traceback.format_exc())
" 2>&1
echo "[entrypoint] === 探针完成 ==="

# 启动 server
echo "[entrypoint] 启动 server.py..."
exec python server.py
