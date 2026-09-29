"""开发 / 测试种子数据。使用超级用户连接（绕过 RLS），生产与预发环境拒绝运行。

创建：
  演示企业：admin@demo.local（admin）、bd.a@demo.local（bd）、bd.b@demo.local（bd）
  隔离测试企业：bd.c@other.local（bd）
"""

import sys
import uuid
from dataclasses import dataclass

from sqlalchemy import create_engine, text

from tk_workspace.config import get_settings
from tk_workspace.modules.identity.security import hash_password

NS = uuid.UUID("6f1c7a52-6d0e-4a2b-9c55-0b8f3f0b7d10")


def sid(name: str) -> uuid.UUID:
    """稳定 ID：重复运行种子脚本不会产生重复数据。"""
    return uuid.uuid5(NS, name)


@dataclass(frozen=True)
class SeedUser:
    email: str
    name: str
    tenant: str
    role: str


TENANTS = {"demo": ("演示企业", "Europe/London"), "other": ("隔离测试企业", "Europe/Berlin")}
USERS = [
    SeedUser("admin@demo.local", "演示管理员", "demo", "admin"),
    SeedUser("bd.a@demo.local", "BD 小A", "demo", "bd"),
    SeedUser("bd.b@demo.local", "BD 小B", "demo", "bd"),
    SeedUser("bd.c@other.local", "BD 小C", "other", "bd"),
]


def seed(dsn: str | None = None, password: str | None = None) -> dict[str, dict[str, str]]:
    settings = get_settings()
    if not settings.is_dev_like:
        raise SystemExit("seed_dev 只能在 development / test 环境运行")
    password = password or settings.seed_dev_password.get_secret_value()
    if not password:
        raise SystemExit("请在 .env 设置 SEED_DEV_PASSWORD")
    engine = create_engine(dsn or settings.superuser_sync_dsn)
    pw_hash = hash_password(password)
    ids: dict[str, dict[str, str]] = {}
    with engine.begin() as c:
        for key, (name, tz) in TENANTS.items():
            c.execute(
                text(
                    "INSERT INTO tenants (id, name, time_zone) VALUES (:id, :n, :tz) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": sid(f"tenant:{key}"), "n": name, "tz": tz},
            )
        for u in USERS:
            uid, pid, mid = sid(f"user:{u.email}"), sid(f"principal:{u.email}"), sid(f"membership:{u.email}")
            tid = sid(f"tenant:{u.tenant}")
            c.execute(
                text(
                    """INSERT INTO users (id, identity_provider, provider_subject, display_name)
                       VALUES (:id, 'local', :s, :n) ON CONFLICT (id) DO NOTHING"""
                ),
                {"id": uid, "s": u.email, "n": u.name},
            )
            c.execute(
                text(
                    """INSERT INTO password_credentials (user_id, password_hash) VALUES (:u, :h)
                       ON CONFLICT (user_id) DO UPDATE SET password_hash = EXCLUDED.password_hash, updated_at = now()"""
                ),
                {"u": uid, "h": pw_hash},
            )
            c.execute(
                text(
                    """INSERT INTO tenant_principals (id, tenant_id, kind, user_id)
                       VALUES (:id, :t, 'user', :u) ON CONFLICT (id) DO NOTHING"""
                ),
                {"id": pid, "t": tid, "u": uid},
            )
            c.execute(
                text(
                    """INSERT INTO memberships (id, tenant_id, principal_id, role)
                       VALUES (:id, :t, :p, :r) ON CONFLICT (id) DO NOTHING"""
                ),
                {"id": mid, "t": tid, "p": pid, "r": u.role},
            )
            ids[u.email] = {"user_id": str(uid), "principal_id": str(pid), "tenant_id": str(tid)}
    engine.dispose()
    return ids


if __name__ == "__main__":
    result = seed()
    for email in result:
        print(f"seeded {email}")
    print("密码为 .env 中的 SEED_DEV_PASSWORD", file=sys.stderr)
