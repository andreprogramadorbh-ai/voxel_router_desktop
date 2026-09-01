"""Autenticação local: Argon2id, sessões com hash e limitação contra força bruta."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.database import Database

PASSWORD_HASHER = PasswordHasher()
MAX_ATTEMPTS = 5
LOCK_WINDOW_MINUTES = 15


class AuthenticationError(RuntimeError):
    """Erro genérico intencional para não revelar credenciais inválidas."""


class AccountLockedError(AuthenticationError):
    pass


class AuthenticationService:
    def __init__(self, database: Database, session_minutes: int = 30) -> None:
        self.database = database
        self.session_minutes = session_minutes

    def has_administrator(self) -> bool:
        row = self.database.query_one("SELECT 1 FROM users WHERE is_active = 1 LIMIT 1")
        return row is not None

    def provision_administrator(self, username: str, password: str) -> int:
        username = self._validate_username(username)
        self._validate_password(password)
        with self.database.transaction() as connection:
            if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise ValueError("Já existe um administrador provisionado")
            cursor = connection.execute(
                "INSERT INTO users(username, password_hash, must_change_password) VALUES (?, ?, 1)",
                (username, PASSWORD_HASHER.hash(password)),
            )
            return int(cursor.lastrowid)

    def login(self, username: str, password: str, source_ip: str = "127.0.0.1") -> dict[str, Any]:
        normalized = username.strip().lower()
        if self._is_locked(normalized, source_ip):
            self._record_attempt(normalized, source_ip, success=False)
            raise AccountLockedError("Usuário ou senha inválidos.")
        row = self.database.query_one(
            "SELECT id, username, password_hash, must_change_password, is_active FROM users WHERE username = ?",
            (normalized,),
        )
        valid = False
        if row and bool(row["is_active"]):
            try:
                valid = PASSWORD_HASHER.verify(row["password_hash"], password)
            except (VerifyMismatchError, InvalidHashError):
                valid = False
        self._record_attempt(normalized, source_ip, success=valid)
        if not valid or row is None:
            raise AuthenticationError("Usuário ou senha inválidos.")
        if PASSWORD_HASHER.check_needs_rehash(row["password_hash"]):
            with self.database.transaction() as connection:
                connection.execute("UPDATE users SET password_hash = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (PASSWORD_HASHER.hash(password), row["id"]))
        with self.database.transaction() as connection:
            connection.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
        token, expires_at = self._create_session(int(row["id"]), source_ip)
        self.audit(int(row["id"]), "LOGIN", "SESSION", None, source_ip, "SUCCESS")
        return {"token": token, "expires_at": expires_at.isoformat(), "must_change_password": bool(row["must_change_password"]), "username": row["username"]}

    def current_user(self, token: str) -> dict[str, Any] | None:
        token_hash = self._hash_token(token)
        row = self.database.query_one(
            """SELECT u.id, u.username, u.must_change_password FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.revoked_at IS NULL AND s.expires_at > CURRENT_TIMESTAMP AND u.is_active = 1""",
            (token_hash,),
        )
        return dict(row) if row else None

    def logout(self, token: str, source_ip: str = "127.0.0.1") -> None:
        user = self.current_user(token)
        with self.database.transaction() as connection:
            connection.execute("UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP WHERE token_hash = ?", (self._hash_token(token),))
        if user:
            self.audit(int(user["id"]), "LOGOUT", "SESSION", None, source_ip, "SUCCESS")

    def change_password(self, user_id: int, current_password: str, new_password: str, source_ip: str = "127.0.0.1") -> None:
        self._validate_password(new_password)
        row = self.database.query_one("SELECT password_hash FROM users WHERE id = ? AND is_active = 1", (user_id,))
        if row is None:
            raise AuthenticationError("Usuário ou senha inválidos.")
        try:
            if not PASSWORD_HASHER.verify(row["password_hash"], current_password):
                raise AuthenticationError("Usuário ou senha inválidos.")
        except (VerifyMismatchError, InvalidHashError) as exc:
            raise AuthenticationError("Usuário ou senha inválidos.") from exc
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (PASSWORD_HASHER.hash(new_password), user_id),
            )
            connection.execute("UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP WHERE user_id = ?", (user_id,))
        self.audit(user_id, "CHANGE_PASSWORD", "USER", str(user_id), source_ip, "SUCCESS")

    def update_username(self, user_id: int, username: str, source_ip: str = "127.0.0.1") -> None:
        normalized = self._validate_username(username)
        with self.database.transaction() as connection:
            connection.execute("UPDATE users SET username = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (normalized, user_id))
        self.audit(user_id, "CHANGE_USERNAME", "USER", str(user_id), source_ip, "SUCCESS")

    def audit(self, user_id: int | None, action: str, entity_type: str, entity_id: str | None, source_ip: str, result: str, details: str | None = None) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO audit_logs(user_id, action, entity_type, entity_id, source_ip, result, details) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, action, entity_type, entity_id, source_ip, result, details),
            )

    def _create_session(self, user_id: int, source_ip: str) -> tuple[str, datetime]:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(minutes=self.session_minutes)
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO sessions(user_id, token_hash, expires_at, source_ip) VALUES (?, ?, ?, ?)",
                (user_id, self._hash_token(token), expires_at.strftime("%Y-%m-%d %H:%M:%S"), source_ip),
            )
        return token, expires_at

    def _is_locked(self, username: str, source_ip: str) -> bool:
        row = self.database.query_one(
            """SELECT COUNT(*) AS count FROM auth_attempts
               WHERE username = ? AND source_ip = ? AND success = 0
               AND attempted_at >= datetime('now', ?)""",
            (username, source_ip, f"-{LOCK_WINDOW_MINUTES} minutes"),
        )
        return bool(row and int(row["count"]) >= MAX_ATTEMPTS)

    def _record_attempt(self, username: str, source_ip: str, success: bool) -> None:
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO auth_attempts(username, source_ip, success) VALUES (?, ?, ?)", (username, source_ip, int(success)))

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_username(username: str) -> str:
        normalized = username.strip().lower()
        if not 3 <= len(normalized) <= 64 or not normalized.replace("_", "").replace("-", "").isalnum():
            raise ValueError("O usuário deve ter entre 3 e 64 caracteres alfanuméricos")
        return normalized

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(password) < 12 or not any(char.islower() for char in password) or not any(char.isupper() for char in password) or not any(char.isdigit() for char in password) or not any(not char.isalnum() for char in password):
            raise ValueError("A senha deve ter 12 caracteres e conter maiúscula, minúscula, número e símbolo")
