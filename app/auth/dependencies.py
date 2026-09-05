from typing import Annotated

import jwt
from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import decode_access_token
from app.db.models import User
from app.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    session: SessionDep,
    access_token: Annotated[str | None, Cookie()] = None,
) -> User:
    if access_token is None:
        raise HTTPException(status_code=401, detail="not authenticated")

    try:
        payload = decode_access_token(access_token)
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="invalid or expired session") from exc

    user = await session.get(User, payload.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    return user
