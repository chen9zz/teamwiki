from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, field_validator

Role = Literal["owner", "admin", "editor", "viewer"]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()


class RegisterInput(Credentials):
    name: Name


class UserOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    name: str


class AuthOutput(BaseModel):
    user: UserOutput


class WorkspaceInput(BaseModel):
    name: Name


class WorkspaceOutput(BaseModel):
    id: str
    name: str
    role: Role


class MemberOutput(BaseModel):
    user_id: str
    email: str
    name: str
    role: Role


class MemberUpdate(BaseModel):
    role: Role


class InvitationInput(BaseModel):
    email: EmailStr
    role: Role

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.lower()


class InvitationOutput(BaseModel):
    id: str
    workspace_id: str
    email: str
    role: Role
    expires_at: datetime
    token: str


class InvitationAccept(BaseModel):
    token: str = Field(min_length=20, max_length=256)
