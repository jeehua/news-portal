"""
jeeseek 鉴权模块
- 密码哈希：bcrypt cost=12
- Token：JWT HS256，存于 httpOnly cookie
- Session 表：user_sessions 用于服务端吊销
- 邀请码：12 位 Base32 随机串

依赖：
    pip install bcrypt PyJWT
"""
import os
import secrets
import string
import time
import uuid
from typing import Optional

import bcrypt
import jwt
from fastapi import Cookie, Depends, HTTPException, Request, Response, status

from db import connect, USE_POSTGRES

# ────────────── 配置 ──────────────
JWT_SECRET = os.getenv("JWT_SECRET", "jeeseek-dev-secret-change-me-in-prod-2026")
JWT_ALGO = "HS256"
JWT_TTL_SEC = 7 * 24 * 3600  # 7 天
COOKIE_NAME = "jeeseek_token"
INVITE_CODE_LEN = 12
BOOTSTRAP_NOTE = "auto-bootstrap"
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "1") == "1"  # 生产 1，本地 0


# ────────────── 密码 ──────────────
def hash_password(plain: str) -> str:
    """bcrypt cost=12。~250ms，够防暴力"""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    if not hashed or not plain:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ────────────── 邀请码 ──────────────
def _invite_alphabet() -> str:
    # 去掉了容易混淆的 0/O/1/I/L
    return "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_invite_code(length: int = INVITE_CODE_LEN) -> str:
    """生成 12 位无歧义随机码，比如 'K7H3J9X2M4N8'"""
    alphabet = _invite_alphabet()
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ────────────── JWT ──────────────
def create_token(user_id: int, username: str, role: str) -> str:
    now = int(time.time())
    jti = uuid.uuid4().hex
    payload = {
        "sub": str(user_id),
        "u": username,
        "r": role,
        "iat": now,
        "exp": now + JWT_TTL_SEC,
        "jti": jti,
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)
    # 同步落库（用于吊销）
    _persist_session(jti, user_id, now, now + JWT_TTL_SEC)
    return token


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def _persist_session(jti: str, user_id: int, issued_at: int, expires_at: int) -> None:
    conn = connect()
    cur = conn.cursor()
    try:
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO user_sessions (jti, user_id, issued_at, expires_at) VALUES (%s,%s,%s,%s) ON CONFLICT (jti) DO NOTHING",
                (jti, user_id, issued_at, expires_at),
            )
        else:
            cur.execute(
                "INSERT OR IGNORE INTO user_sessions (jti, user_id, issued_at, expires_at) VALUES (?,?,?,?)",
                (jti, user_id, issued_at, expires_at),
            )
        conn.commit()
    finally:
        conn.close()


def revoke_session(jti: str) -> None:
    conn = connect()
    cur = conn.cursor()
    try:
        if USE_POSTGRES:
            cur.execute("UPDATE user_sessions SET revoked=1 WHERE jti=%s", (jti,))
        else:
            cur.execute("UPDATE user_sessions SET revoked=1 WHERE jti=?", (jti,))
        conn.commit()
    finally:
        conn.close()


def is_session_valid(jti: str) -> bool:
    conn = connect()
    cur = conn.cursor()
    try:
        if USE_POSTGRES:
            cur.execute(
                "SELECT revoked FROM user_sessions WHERE jti=%s AND expires_at > %s",
                (jti, int(time.time())),
            )
        else:
            cur.execute(
                "SELECT revoked FROM user_sessions WHERE jti=? AND expires_at > ?",
                (jti, int(time.time())),
            )
        row = cur.fetchone()
        if not row:
            return False
        # Postgres: row[0] 是 bool/数字；SQLite: 整数
        return not row[0]
    finally:
        conn.close()


# ────────────── 用户查询 ──────────────
def get_user_by_id(user_id: int) -> Optional[dict]:
    conn = connect()
    cur = conn.cursor()
    try:
        if USE_POSTGRES:
            cur.execute(
                "SELECT id, username, email, display_name, role, invite_quota, invited_by, created_at, last_login_at, status FROM users WHERE id=%s",
                (user_id,),
            )
        else:
            cur.execute(
                "SELECT id, username, email, display_name, role, invite_quota, invited_by, created_at, last_login_at, status FROM users WHERE id=?",
                (user_id,),
            )
        row = cur.fetchone()
        if not row:
            return None
        return _row_to_user(row)
    finally:
        conn.close()


def get_user_by_username(username: str) -> Optional[dict]:
    conn = connect()
    cur = conn.cursor()
    try:
        if USE_POSTGRES:
            cur.execute(
                "SELECT id, username, email, password_hash, display_name, role, invite_quota, invited_by, created_at, last_login_at, status FROM users WHERE username=%s",
                (username,),
            )
        else:
            cur.execute(
                "SELECT id, username, email, password_hash, display_name, role, invite_quota, invited_by, created_at, last_login_at, status FROM users WHERE username=?",
                (username,),
            )
        row = cur.fetchone()
        if not row:
            return None
        u = _row_to_user(row)
        u["password_hash"] = row[3]
        return u
    finally:
        conn.close()


def get_user_by_email(email: str) -> Optional[dict]:
    conn = connect()
    cur = conn.cursor()
    try:
        if USE_POSTGRES:
            cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        else:
            cur.execute("SELECT id FROM users WHERE email=?", (email,))
        row = cur.fetchone()
        return {"id": row[0]} if row else None
    finally:
        conn.close()


def _row_to_user(row) -> dict:
    """统一把 DB 行转成 dict（不含 password_hash）"""
    return {
        "id": row[0],
        "username": row[1],
        "email": row[2],
        "display_name": row[4] if len(row) > 4 else None,
        "role": row[5] if len(row) > 5 else "user",
        "invite_quota": row[6] if len(row) > 6 else 0,
        "invited_by": row[7] if len(row) > 7 else None,
        "created_at": row[8] if len(row) > 8 else 0,
        "last_login_at": row[9] if len(row) > 9 else None,
        "status": row[10] if len(row) > 10 else "active",
    }


def count_users() -> int:
    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM users")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def update_last_login(user_id: int) -> None:
    conn = connect()
    cur = conn.cursor()
    try:
        if USE_POSTGRES:
            cur.execute("UPDATE users SET last_login_at=%s WHERE id=%s", (int(time.time()), user_id))
        else:
            cur.execute("UPDATE users SET last_login_at=? WHERE id=?", (int(time.time()), user_id))
        conn.commit()
    finally:
        conn.close()


# ────────────── 邀请码操作 ──────────────
def get_invite_code(code: str) -> Optional[dict]:
    conn = connect()
    cur = conn.cursor()
    try:
        if USE_POSTGRES:
            cur.execute(
                "SELECT id, code, created_by, used_by, max_uses, use_count, note, created_at, used_at, expires_at, status FROM invite_codes WHERE code=%s",
                (code,),
            )
        else:
            cur.execute(
                "SELECT id, code, created_by, used_by, max_uses, use_count, note, created_at, used_at, expires_at, status FROM invite_codes WHERE code=?",
                (code,),
            )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "code": row[1],
            "created_by": row[2],
            "used_by": row[3],
            "max_uses": row[4],
            "use_count": row[5],
            "note": row[6],
            "created_at": row[7],
            "used_at": row[8],
            "expires_at": row[9],
            "status": row[10],
        }
    finally:
        conn.close()


def consume_invite_code(code: str, used_by_user_id: int) -> bool:
    """原子地：把邀请码的 use_count+1 并标记使用人。返回是否成功"""
    conn = connect()
    cur = conn.cursor()
    try:
        if USE_POSTGRES:
            cur.execute(
                "UPDATE invite_codes SET use_count=use_count+1, used_by=%s, used_at=%s, status=CASE WHEN use_count+1>=max_uses THEN 'exhausted' ELSE status END WHERE code=%s AND status='active' AND (expires_at IS NULL OR expires_at > %s) AND use_count < max_uses",
                (used_by_user_id, int(time.time()), code, int(time.time())),
            )
        else:
            cur.execute(
                "UPDATE invite_codes SET use_count=use_count+1, used_by=?, used_at=?, status=CASE WHEN use_count+1>=max_uses THEN 'exhausted' ELSE status END WHERE code=? AND status='active' AND (expires_at IS NULL OR expires_at > ?) AND use_count < max_uses",
                (used_by_user_id, int(time.time()), code, int(time.time())),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def create_invite_code(created_by: int, max_uses: int = 1, note: str = "", expires_in_days: Optional[int] = None) -> str:
    """创建邀请码；返回 code 字符串"""
    code = generate_invite_code()
    # 极少概率冲突，重试
    for _ in range(5):
        if not get_invite_code(code):
            break
        code = generate_invite_code()
    expires_at = int(time.time()) + expires_in_days * 86400 if expires_in_days else None
    conn = connect()
    cur = conn.cursor()
    try:
        if USE_POSTGRES:
            cur.execute(
                "INSERT INTO invite_codes (code, created_by, max_uses, note, created_at, expires_at) VALUES (%s,%s,%s,%s,%s,%s)",
                (code, created_by, max_uses, note, int(time.time()), expires_at),
            )
        else:
            cur.execute(
                "INSERT INTO invite_codes (code, created_by, max_uses, note, created_at, expires_at) VALUES (?,?,?,?,?,?)",
                (code, created_by, max_uses, note, int(time.time()), expires_at),
            )
        conn.commit()
        return code
    finally:
        conn.close()


def list_my_invite_codes(user_id: int) -> list:
    conn = connect()
    cur = conn.cursor()
    try:
        if USE_POSTGRES:
            cur.execute(
                "SELECT code, max_uses, use_count, note, created_at, expires_at, status FROM invite_codes WHERE created_by=%s ORDER BY created_at DESC",
                (user_id,),
            )
        else:
            cur.execute(
                "SELECT code, max_uses, use_count, note, created_at, expires_at, status FROM invite_codes WHERE created_by=? ORDER BY created_at DESC",
                (user_id,),
            )
        rows = cur.fetchall()
        return [
            {
                "code": r[0],
                "max_uses": r[1],
                "use_count": r[2],
                "note": r[3],
                "created_at": r[4],
                "expires_at": r[5],
                "status": r[6],
            }
            for r in rows
        ]
    finally:
        conn.close()


def list_users_i_invited(user_id: int) -> list:
    conn = connect()
    cur = conn.cursor()
    try:
        if USE_POSTGRES:
            cur.execute(
                "SELECT id, username, display_name, created_at FROM users WHERE invited_by=%s ORDER BY created_at DESC",
                (user_id,),
            )
        else:
            cur.execute(
                "SELECT id, username, display_name, created_at FROM users WHERE invited_by=? ORDER BY created_at DESC",
                (user_id,),
            )
        rows = cur.fetchall()
        return [
            {"id": r[0], "username": r[1], "display_name": r[2], "created_at": r[3]}
            for r in rows
        ]
    finally:
        conn.close()


# ────────────── FastAPI 依赖 ──────────────
async def get_current_user(request: Request) -> Optional[dict]:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    jti = payload.get("jti")
    if jti and not is_session_valid(jti):
        return None
    user_id = int(payload.get("sub", 0))
    if not user_id:
        return None
    user = get_user_by_id(user_id)
    if not user or user.get("status") != "active":
        return None
    return user


async def require_login(user: Optional[dict] = Depends(get_current_user)) -> dict:
    """需要登录的接口用这个 Depends"""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要登录",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user


async def require_admin(user: dict = Depends(require_login)) -> dict:
    """需要 admin 权限的接口用这个 Depends"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


def set_auth_cookie(response: Response, token: str, max_age: int = JWT_TTL_SEC) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")
