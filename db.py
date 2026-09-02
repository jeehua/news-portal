"""
数据库抽象层
- Render 部署 + Supabase → Postgres (psycopg2)
- 本地开发 → SQLite (向后兼容)

用法：
    from db import _db, init_schema, now_unix
    
    init_schema()  # 启动时调用一次
    
    conn = _db.connect()
    cur = conn.cursor()
    cur.execute('SELECT 1')
    row = cur.fetchone()
    conn.commit()
    conn.close()
"""
import os
import sqlite3
import time
from pathlib import Path

DATABASE_URL = os.getenv('DATABASE_URL', '').strip()

# Supabase / Render Postgres URL 格式：
#   postgresql://postgres:password@host:5432/postgres
#   postgres://postgres:password@host:5432/postgres (legacy)
USE_POSTGRES = DATABASE_URL.startswith(('postgres://', 'postgresql://'))

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2 = psycopg2
    print(f'[db] 使用 Postgres: {DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else "(隐藏)"}')
else:
    _PSYCOPG2 = None
    STATS_DIR = Path(os.getenv('STATS_DIR', '/app/data'))
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    STATS_DB = STATS_DIR / 'stats.db'
    print(f'[db] 使用 SQLite: {STATS_DB}')


def connect():
    """获取数据库连接"""
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        conn.autocommit = False
        return conn
    else:
        return sqlite3.connect(str(STATS_DB), timeout=5)


def init_schema():
    """创建表结构（SQLite / Postgres 兼容）"""
    conn = connect()
    c = conn.cursor()
    if USE_POSTGRES:
        # Postgres: SERIAL PRIMARY KEY / TEXT / INTEGER
        c.execute('''CREATE TABLE IF NOT EXISTS visit_logs (
            id SERIAL PRIMARY KEY,
            ts BIGINT NOT NULL,
            ip TEXT, country TEXT, device TEXT, browser TEXT,
            path TEXT, referrer TEXT, session_id TEXT, duration INTEGER
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_ts ON visit_logs(ts)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_session ON visit_logs(session_id)')
        c.execute('''CREATE TABLE IF NOT EXISTS news_clicks (
            news_id TEXT PRIMARY KEY,
            source_id TEXT,
            title TEXT,
            url TEXT,
            click_count INTEGER DEFAULT 0,
            last_click_ts BIGINT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS source_stats (
            source_id TEXT PRIMARY KEY,
            date TEXT,
            success_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0
        )''')
    else:
        # SQLite: INTEGER PRIMARY KEY AUTOINCREMENT
        c.execute('''CREATE TABLE IF NOT EXISTS visit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            ip TEXT, country TEXT, device TEXT, browser TEXT,
            path TEXT, referrer TEXT, session_id TEXT, duration INTEGER
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_ts ON visit_logs(ts)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_session ON visit_logs(session_id)')
        c.execute('''CREATE TABLE IF NOT EXISTS news_clicks (
            news_id TEXT PRIMARY KEY,
            source_id TEXT,
            title TEXT,
            url TEXT,
            click_count INTEGER DEFAULT 0,
            last_click_ts INTEGER
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS source_stats (
            source_id TEXT PRIMARY KEY,
            date TEXT,
            success_count INTEGER DEFAULT 0,
            fail_count INTEGER DEFAULT 0
        )''')
    conn.commit()
    conn.close()


# ────────────── 时区工具（统一 SQLite/Postgres）──────────────
TZ_OFFSET_SEC = 8 * 3600  # Asia/Shanghai = UTC+8

def hour_in_cn(ts: int) -> str:
    """返回 ts 对应的 Asia/Shanghai 小时字符串 '00' ~ '23'"""
    if USE_POSTGRES:
        # Postgres: to_char(to_timestamp(ts) AT TIME ZONE 'Asia/Shanghai', 'HH24')
        # 但简单起见在 Python 算
        dt = time.gmtime(ts + TZ_OFFSET_SEC)
        return f'{dt.tm_hour:02d}'
    else:
        dt = time.gmtime(ts + TZ_OFFSET_SEC)
        return f'{dt.tm_hour:02d}'

def date_in_cn(ts: int) -> str:
    """返回 ts 对应的 Asia/Shanghai 日期字符串 'YYYY-MM-DD'"""
    if USE_POSTGRES:
        dt = time.gmtime(ts + TZ_OFFSET_SEC)
        return f'{dt.tm_year:04d}-{dt.tm_mon:02d}-{dt.tm_mday:02d}'
    else:
        dt = time.gmtime(ts + TZ_OFFSET_SEC)
        return f'{dt.tm_year:04d}-{dt.tm_mon:02d}-{dt.tm_mday:02d}'