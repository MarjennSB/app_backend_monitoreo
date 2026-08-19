from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

class RoleModel(BaseModel):
    id: int
    name: str
    status: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    @classmethod
    def from_record(cls, record) -> "RoleModel":
        return cls(**dict(record))

class UserModel(BaseModel):
    id: int
    role_id: int
    username: str
    first_name: str
    last_name: Optional[str] = ''
    email: str
    password: str
    status: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    @classmethod
    def from_record(cls, record) -> "UserModel":
        return cls(**dict(record))

class UserCreate(BaseModel):
    username: str = Field(..., min_length=3)
    first_name: str = Field(..., min_length=2)
    last_name: Optional[str] = ''
    email: str = Field(..., min_length=5)
    password: str = Field(..., min_length=6)
    role_ids: list[int] = [2]

class UserUpdate(BaseModel):
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    role_ids: Optional[list[int]] = None

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class UserResponse(BaseModel):
    id: int
    username: str
    first_name: str
    last_name: str
    email: str
    role_id: int
    status: bool
