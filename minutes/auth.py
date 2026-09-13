"""Invitation-only accounts and revocable sessions for a shared department library."""

from contextlib import contextmanager
import hashlib
import hmac
from pathlib import Path
import re
import secrets
import sqlite3
import time


class AuthError(ValueError):
    pass


def password_hash(password: str, salt: str) -> str:
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()


class Accounts:
    def __init__(self, path: Path, clock=time.time):
        self.path, self.clock = path, clock
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY, salt TEXT NOT NULL, password_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    digest TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    username TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL
                );
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def register(self, username: str, password: str, invitation: str, expected: str):
        if len(expected) < 16:
            raise AuthError("管理员尚未配置有效的部门邀请码，暂不能注册。")
        if not hmac.compare_digest(invitation.encode(), expected.encode()):
            raise AuthError("部门邀请码不正确。")
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,31}", username):
            raise AuthError("账号需为 3–32 位英文字母、数字、下划线、点或短横线。")
        if not 12 <= len(password) <= 128:
            raise AuthError("密码需为 12–128 个字符。")
        salt = secrets.token_hex(16)
        digest = password_hash(password, salt)
        try:
            with self.db() as db:
                db.execute("INSERT INTO users VALUES (?, ?, ?)", (username, salt, digest))
        except sqlite3.IntegrityError:
            raise AuthError("账号已存在，请更换账号或登录。") from None

    def login(self, username: str, password: str) -> str:
        username = username.strip().lower()[:256]
        if len(password) > 128:
            raise AuthError("账号或密码不正确。")
        stamp = self.clock()
        failure = None
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE until<=?", (stamp,))
            db.execute("DELETE FROM sessions WHERE expires<=?", (stamp,))
            attempt = db.execute("SELECT * FROM attempts WHERE username=?", (username,)).fetchone()
            if attempt and attempt["count"] >= 5:
                raise AuthError("尝试次数过多，请 10 分钟后再试。")
            user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            # Run the same expensive hash for unknown users, without leaking account existence.
            salt = user["salt"] if user else "00" * 16
            digest = password_hash(password, salt)
            if not user or not hmac.compare_digest(digest, user["password_hash"]):
                db.execute("INSERT INTO attempts VALUES (?, 1, ?) ON CONFLICT(username) DO UPDATE SET count=count+1",
                           (username, stamp + 600))
                failure = "账号或密码不正确。"
            else:
                db.execute("DELETE FROM attempts WHERE username=?", (username,))
                token = secrets.token_urlsafe(32)
                db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                           (hashlib.sha256(token.encode()).hexdigest(), username, stamp + 12 * 3600))
        # Raise after committing the failed-attempt counter.
        if failure:
            raise AuthError(failure)
        return token

    def user(self, token: str) -> str | None:
        if not token:
            return None
        with self.db() as db:
            row = db.execute("SELECT username FROM sessions WHERE digest=? AND expires>?",
                             (hashlib.sha256(token.encode()).hexdigest(), self.clock())).fetchone()
        return row["username"] if row else None

    def logout(self, token: str):
        with self.db() as db:
            db.execute("DELETE FROM sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),))
