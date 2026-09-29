"""RLS 隔离验收：BD 之间、企业之间互相看不到数据；无上下文时零行；运行角色不能绕过 RLS。"""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.integration.helpers import ctx_of, rows_as
from tk_workspace.config import get_settings
from tk_workspace.platform.db.session import runtime_tx_sync

pytestmark = pytest.mark.integration


def test_runtime_role_cannot_bypass_rls(seeded):
    with runtime_tx_sync() as s:
        r = s.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
    assert r.rolsuper is False and r.rolbypassrls is False


def test_all_tenant_tables_force_rls(seeded):
    engine = create_engine(get_settings().superuser_sync_dsn)
    with engine.connect() as c:
        rows = c.execute(
            text(
                """SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, pg_get_userbyid(c.relowner) AS owner
                   FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE n.nspname = 'public' AND c.relkind = 'r'
                     AND EXISTS (SELECT 1 FROM information_schema.columns col
                                 WHERE col.table_name = c.relname AND col.column_name = 'tenant_id')"""
            )
        ).all()
    engine.dispose()
    assert rows, "expected tenant tables"
    for r in rows:
        assert r.relrowsecurity and r.relforcerowsecurity, r.relname
        assert r.owner == "app_owner", r.relname


def test_no_context_sees_nothing(seeded):
    for table in ("tenants", "tenant_principals", "memberships", "jobs", "users", "sessions"):
        sql = f"SELECT 1 FROM {table}"  # noqa: S608 — 表名来自上面的常量元组
        assert rows_as({}, sql) == [], table


def _insert_job(ctx: dict[str, str]) -> str:
    job_id = str(uuid.uuid4())
    with runtime_tx_sync(ctx) as s:
        s.execute(
            text(
                """INSERT INTO jobs (id, tenant_id, kind, owner_principal_id, requested_by)
                   VALUES (:id, :t, 'system.noop', :p, :p)"""
            ),
            {"id": job_id, "t": ctx["app.tenant_id"], "p": ctx["app.principal_id"]},
        )
    return job_id


def test_bd_cannot_see_other_bd_jobs(seeded):
    a, b = ctx_of(seeded, "bd.a@demo.local"), ctx_of(seeded, "bd.b@demo.local")
    admin = ctx_of(seeded, "admin@demo.local")
    job_id = _insert_job(a)
    assert rows_as(a, "SELECT id FROM jobs WHERE id = :id", {"id": job_id})
    assert rows_as(b, "SELECT id FROM jobs WHERE id = :id", {"id": job_id}) == []
    # 管理员角色也不因此自动获得 BD 私有业务记录（ADR-06）
    assert rows_as(admin, "SELECT id FROM jobs WHERE id = :id", {"id": job_id}) == []


def test_other_tenant_sees_nothing(seeded):
    a, c = ctx_of(seeded, "bd.a@demo.local"), ctx_of(seeded, "bd.c@other.local")
    job_id = _insert_job(a)
    assert rows_as(c, "SELECT id FROM jobs WHERE id = :id", {"id": job_id}) == []
    demo_tenant = seeded["bd.a@demo.local"]["tenant_id"]
    assert rows_as(c, "SELECT id FROM tenants WHERE id = :id", {"id": demo_tenant}) == []
    assert rows_as(c, "SELECT id FROM tenant_principals WHERE tenant_id = :id", {"id": demo_tenant}) == []


def test_cannot_insert_into_other_tenant(seeded):
    c = ctx_of(seeded, "bd.c@other.local")
    forged = {**c, "app.tenant_id": seeded["bd.a@demo.local"]["tenant_id"]}
    with pytest.raises(DBAPIError):
        _insert_job(forged)


def test_cannot_create_job_for_another_principal(seeded):
    a, b = ctx_of(seeded, "bd.a@demo.local"), ctx_of(seeded, "bd.b@demo.local")
    with pytest.raises(DBAPIError), runtime_tx_sync(a) as s:
        s.execute(
            text(
                """INSERT INTO jobs (id, tenant_id, kind, owner_principal_id, requested_by)
                   VALUES (:id, :t, 'system.noop', :p, :p)"""
            ),
            {"id": str(uuid.uuid4()), "t": a["app.tenant_id"], "p": b["app.principal_id"]},
        )


def test_runtime_role_has_no_delete(seeded):
    a = ctx_of(seeded, "bd.a@demo.local")
    with pytest.raises(DBAPIError), runtime_tx_sync(a) as s:
        s.execute(text("DELETE FROM jobs"))
