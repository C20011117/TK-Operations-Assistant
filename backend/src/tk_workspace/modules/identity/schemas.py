from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    # 登录只做宽松校验（不拒绝 .local 等内部域名），账号是否存在由服务层判断且统一返回通用错误
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+$")
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return v.strip().lower()


class TenantMembership(BaseModel):
    tenant_id: UUID
    tenant_name: str
    principal_id: UUID
    role: Literal["admin", "bd", "viewer"]


class Me(BaseModel):
    user_id: UUID
    display_name: str
    email: str
    current: TenantMembership | None
    memberships: list[TenantMembership]


class SwitchTenantRequest(BaseModel):
    tenant_id: UUID
