import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.settings import settings

_JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


@dataclass(frozen=True)
class AccessTokenPayload:
    user_id: str
    role: str


def create_access_token(user_id: str, role: str) -> str:
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.jwt_access_token_expires_minutes)
    payload = {"sub": user_id, "role": role, "exp": expires_at}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=_JWT_ALGORITHM)


def decode_access_token(token: str) -> AccessTokenPayload:
    """Raises jwt.PyJWTError (expired, bad signature, malformed) - callers turn that into a 401."""

    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[_JWT_ALGORITHM])
    return AccessTokenPayload(user_id=payload["sub"], role=payload["role"])


def new_refresh_token() -> tuple[str, str, datetime]:
    """Returns (raw_token, token_hash, expires_at). The raw token is what goes in the cookie and
    is never stored; only its hash is persisted (app.db.models.RefreshToken) - same reasoning as
    never storing a plaintext password."""

    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(days=settings.jwt_refresh_token_expires_days)
    return raw_token, hash_refresh_token(raw_token), expires_at


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()
