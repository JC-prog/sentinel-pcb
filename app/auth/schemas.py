from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr

from app.db.models import UserRole

# Admin is deliberately excluded - see app/auth/service.py's bootstrap logic.
RegistrableRole = Literal[UserRole.QA, UserRole.OPERATOR]


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    employee_id: str
    department_shift: str
    role: RegistrableRole


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    name: str
    email: str
    employee_id: str
    department_shift: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}
