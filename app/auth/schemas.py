from datetime import datetime

from pydantic import BaseModel, EmailStr

from app.db.models import UserRole


class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    employee_id: str
    department_shift: str
    role: UserRole


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: str
    username: str
    email: str
    employee_id: str
    department_shift: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}
