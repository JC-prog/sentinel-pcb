import jwt as pyjwt
import pytest

from app.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.settings import settings


def test_hash_password_round_trip() -> None:
    hashed = hash_password("correct-horse-battery-staple")
    assert hashed != "correct-horse-battery-staple"
    assert verify_password("correct-horse-battery-staple", hashed)
    assert not verify_password("wrong-password", hashed)


def test_access_token_round_trip() -> None:
    token = create_access_token(user_id="user-1", role="qa")
    payload = decode_access_token(token)
    assert payload.user_id == "user-1"
    assert payload.role == "qa"


def test_expired_access_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "jwt_access_token_expires_minutes", -1)
    token = create_access_token(user_id="user-1", role="qa")

    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_access_token(token)


def test_tampered_access_token_is_rejected() -> None:
    token = create_access_token(user_id="user-1", role="qa")

    with pytest.raises(pyjwt.PyJWTError):
        decode_access_token(token + "tampered")


def test_new_refresh_token_hash_is_deterministic_and_not_the_raw_token() -> None:
    raw, token_hash, _expires_at = new_refresh_token()
    assert token_hash != raw
    assert hash_refresh_token(raw) == token_hash
