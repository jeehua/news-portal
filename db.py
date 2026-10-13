"""
数据库抽象层
- Render 部署 + Supabase → Postgres (psycopg2)
- 本地开发 → SQLite (向后兼容)
"""
import os
import sqlite3
import time
from pathlib import Path

DATABASE_URL = os.getenv('DATABASE_URL', '').strip()

# Supabase / Render Postgres URL 格式
USE_POSTGRES = DATABASE_URL.startswith(('postgres://', 'postgresql://'))

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras
    _PSYCOPG2 = psycopg2
    print(f'[db] 使用 Postgres')
else:
    _PSYCOPG2 = None
    STATS_DIR = Path(os.getenv('STATS_DIR', '/app/data'))
    STATS_DIR.mkdir(parents=True, exist_ok=True)
    STATS_DB = STATS_DIR / 'stats.db'
    print(f'[db] 使用 SQLite: {STATS_DB}')


def connect():
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
        conn.autocommit = False
        return conn
    else:
        return sqlite3.connect(str(STATS_DB), timeout=5)


def _create_postgres(c):
    """Postgres 分支: SERIAL / BIGINT"""
    c.execute('CREATE TABLE IF NOT EXISTS visit_logs ('
        'id SERIAL PRIMARY KEY,'
        'ts BIGINT NOT NULL,'
        'ip TEXT, country TEXT, device TEXT, browser TEXT,'
        'path TEXT, referrer TEXT, session_id TEXT, duration INTEGER'
        ')')
    c.execute('CREATE INDEX IF NOT EXISTS idx_ts ON visit_logs(ts)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_session ON visit_logs(session_id)')
    c.execute('CREATE TABLE IF NOT EXISTS news_clicks ('
        'news_id TEXT PRIMARY KEY,'
        'source_id TEXT,'
        'title TEXT,'
        'url TEXT,'
        'click_count INTEGER DEFAULT 0,'
        'last_click_ts BIGINT'
        ')')
    c.execute('CREATE TABLE IF NOT EXISTS source_stats ('
        'source_id TEXT PRIMARY KEY,'
        'date TEXT,'
        'success_count INTEGER DEFAULT 0,'
        'fail_count INTEGER DEFAULT 0'
        ')')
    c.execute('CREATE TABLE IF NOT EXISTS users ('
        'id SERIAL PRIMARY KEY,'
        'username TEXT UNIQUE NOT NULL,'
        'email TEXT UNIQUE NOT NULL,'
        'password_hash TEXT NOT NULL,'
        'display_name TEXT,'
        'role TEXT DEFAULT \'user\','
        'invite_quota INTEGER DEFAULT 3,'
        'invited_by INTEGER REFERENCES users(id),'
        'created_at BIGINT NOT NULL,'
        'last_login_at BIGINT,'
        'status TEXT DEFAULT \'active\''
        ')')
    c.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_users_invited_by ON users(invited_by)')
    c.execute('CREATE TABLE IF NOT EXISTS invite_codes ('
        'id SERIAL PRIMARY KEY,'
        'code TEXT UNIQUE NOT NULL,'
        'created_by INTEGER NOT NULL REFERENCES users(id),'
        'used_by INTEGER REFERENCES users(id),'
        'max_uses INTEGER DEFAULT 1,'
        'use_count INTEGER DEFAULT 0,'
        'note TEXT,'
        'created_at BIGINT NOT NULL,'
        'used_at BIGINT,'
        'expires_at BIGINT,'
        'status TEXT DEFAULT \'active\''
        ')')
    c.execute('CREATE INDEX IF NOT EXISTS idx_invite_code ON invite_codes(code)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_invite_created_by ON invite_codes(created_by)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_invite_status ON invite_codes(status)')
    c.execute('CREATE TABLE IF NOT EXISTS user_sessions ('
        'jti TEXT PRIMARY KEY,'
        'user_id INTEGER NOT NULL REFERENCES users(id),'
        'issued_at BIGINT NOT NULL,'
        'expires_at BIGINT NOT NULL,'
        'revoked INTEGER DEFAULT 0'
        ')')
    c.execute('CREATE INDEX IF NOT EXISTS idx_session_user ON user_sessions(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_session_expires ON user_sessions(expires_at)')


def _create_sqlite(c):
    """SQLite 分支: INTEGER PRIMARY KEY AUTOINCREMENT"""
    c.execute('CREATE TABLE IF NOT EXISTS visit_logs ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT,'
        'ts INTEGER NOT NULL,'
        'ip TEXT, country TEXT, device TEXT, browser TEXT,'
        'path TEXT, referrer TEXT, session_id TEXT, duration INTEGER'
        ')')
    c.execute('CREATE INDEX IF NOT EXISTS idx_ts ON visit_logs(ts)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_session ON visit_logs(session_id)')
    c.execute('CREATE TABLE IF NOT EXISTS news_clicks ('
        'news_id TEXT PRIMARY KEY,'
        'source_id TEXT,'
        'title TEXT,'
        'url TEXT,'
        'click_count INTEGER DEFAULT 0,'
        'last_click_ts INTEGER'
        ')')
    c.execute('CREATE TABLE IF NOT EXISTS source_stats ('
        'source_id TEXT PRIMARY KEY,'
        'date TEXT,'
        'success_count INTEGER DEFAULT 0,'
        'fail_count INTEGER DEFAULT 0'
        ')')
    c.execute('CREATE TABLE IF NOT EXISTS users ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT,'
        'username TEXT UNIQUE NOT NULL,'
        'email TEXT UNIQUE NOT NULL,'
        'password_hash TEXT NOT NULL,'
        'display_name TEXT,'
        'role TEXT DEFAULT \'user\','
        'invite_quota INTEGER DEFAULT 3,'
        'invited_by INTEGER REFERENCES users(id),'
        'created_at INTEGER NOT NULL,'
        'last_login_at INTEGER,'
        'status TEXT DEFAULT \'active\''
        ')')
    c.execute('CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_users_invited_by ON users(invited_by)')
    c.execute('CREATE TABLE IF NOT EXISTS invite_codes ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT,'
        'code TEXT UNIQUE NOT NULL,'
        'created_by INTEGER NOT NULL REFERENCES users(id),'
        'used_by INTEGER REFERENCES users(id),'
        'max_uses INTEGER DEFAULT 1,'
        'use_count INTEGER DEFAULT 0,'
        'note TEXT,'
        'created_at INTEGER NOT NULL,'
        'used_at INTEGER,'
        'expires_at INTEGER,'
        'status TEXT DEFAULT \'active\''
        ')')
    c.execute('CREATE INDEX IF NOT EXISTS idx_invite_code ON invite_codes(code)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_invite_created_by ON invite_codes(created_by)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_invite_status ON invite_codes(status)')
    c.execute('CREATE TABLE IF NOT EXISTS user_sessions ('
        'jti TEXT PRIMARY KEY,'
        'user_id INTEGER NOT NULL REFERENCES users(id),'
        'issued_at INTEGER NOT NULL,'
        'expires_at INTEGER NOT NULL,'
        'revoked INTEGER DEFAULT 0'
        ')')
    c.execute('CREATE INDEX IF NOT EXISTS idx_session_user ON user_sessions(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_session_expires ON user_sessions(expires_at)')


def init_schema():
    """创建表结构（SQLite / Postgres 兼容）"""
    conn = connect()
    c = conn.cursor()
    if USE_POSTGRES:
        _create_postgres(c)
    else:
        _create_sqlite(c)
    conn.commit()
    conn.close()


# 时区工具
TZ_OFFSET_SEC = 8 * 3600

def hour_in_cn(ts):
    dt = time.gmtime(ts + TZ_OFFSET_SEC)
    return f'{dt.tm_hour:02d}'

def date_in_cn(ts):
    dt = time.gmtime(ts + TZ_OFFSET_SEC)
    return f'{dt.tm_year:04d}-{dt.tm_mon:02d}-{dt.tm_mday:02d}'
