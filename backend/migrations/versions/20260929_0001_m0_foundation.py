"""M0 地基：身份与企业、会话、任务与 outbox、幂等记录、欧洲站点目录，以及 RLS。

表名与字段对齐 docs/architecture/02-data-model.md 第 4、10 节。
所有企业表 ENABLE + FORCE ROW LEVEL SECURITY；运行角色 app_runtime 默认拒绝，
只能在设置了 app.* 事务级上下文后看到被授权的行。

Revision ID: 0001
Revises:
Create Date: 2026-09-29
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

JOB_STATUSES = (
    "'queued','running','waiting_input','cancel_requested','succeeded','partial','failed','cancelled'"
)

STATEMENTS: list[str] = [
    # ---------- 上下文函数（事务级 GUC，由平台代码设置） ----------
    "CREATE SCHEMA app AUTHORIZATION app_owner",
    "GRANT USAGE ON SCHEMA app TO app_runtime, app_dispatcher",
    """CREATE FUNCTION app.setting_uuid(name text) RETURNS uuid LANGUAGE sql STABLE AS $$
         SELECT CAST(NULLIF(current_setting(name, true), '') AS uuid) $$""",
    """CREATE FUNCTION app.setting_text(name text) RETURNS text LANGUAGE sql STABLE AS $$
         SELECT NULLIF(current_setting(name, true), '') $$""",
    "CREATE FUNCTION app.current_tenant() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT app.setting_uuid('app.tenant_id') $$",
    "CREATE FUNCTION app.current_user_id() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT app.setting_uuid('app.user_id') $$",
    "CREATE FUNCTION app.current_principal() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT app.setting_uuid('app.principal_id') $$",
    "CREATE FUNCTION app.claim_job_id() RETURNS uuid LANGUAGE sql STABLE AS $$ SELECT app.setting_uuid('app.claim_job_id') $$",
    "CREATE FUNCTION app.login_subject() RETURNS text LANGUAGE sql STABLE AS $$ SELECT lower(app.setting_text('app.login_subject')) $$",
    "CREATE FUNCTION app.session_token_hash() RETURNS text LANGUAGE sql STABLE AS $$ SELECT app.setting_text('app.session_token_hash') $$",
    "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA app TO app_runtime, app_dispatcher",
    # ---------- 全局站点目录 ----------
    """CREATE TABLE markets (
        market_code        text PRIMARY KEY CHECK (market_code ~ '^[A-Z]{2}$'),
        name_zh            text NOT NULL,
        name_en            text NOT NULL,
        region_group       text NOT NULL CHECK (region_group IN ('europe','americas','asia')),
        settlement_currency char(3) NOT NULL,
        default_time_zone  text NOT NULL,
        content_languages  text[] NOT NULL,
        fastmoss_region    text NULL,
        data_status        text NOT NULL CHECK (data_status IN
                             ('queryable','queryable_currency_unknown','platform_unconfirmed','manual_import_only')),
        last_probe_at      timestamptz NULL,
        last_probe_total   integer NULL,
        probe_currency     text NULL,
        notes              text NOT NULL DEFAULT '',
        sort_order         integer NOT NULL DEFAULT 0,
        CONSTRAINT markets_manual_has_no_region CHECK (
            (data_status = 'manual_import_only') = (fastmoss_region IS NULL))
    )""",
    # ---------- 全局登录身份 ----------
    """CREATE TABLE users (
        id                uuid PRIMARY KEY,
        identity_provider text NOT NULL DEFAULT 'local',
        provider_subject  citext NOT NULL,
        display_name      text NOT NULL,
        status            text NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
        created_at        timestamptz NOT NULL DEFAULT now(),
        updated_at        timestamptz NOT NULL DEFAULT now(),
        lock_version      bigint NOT NULL DEFAULT 1,
        UNIQUE (identity_provider, provider_subject)
    )""",
    """CREATE TABLE password_credentials (
        user_id       uuid PRIMARY KEY REFERENCES users(id),
        password_hash text NOT NULL,
        updated_at    timestamptz NOT NULL DEFAULT now()
    )""",
    # ---------- 企业与主体 ----------
    """CREATE TABLE tenants (
        id            uuid PRIMARY KEY,
        name          text NOT NULL,
        status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','closing','closed')),
        time_zone     text NOT NULL DEFAULT 'Europe/London',
        authz_version bigint NOT NULL DEFAULT 1,
        created_at    timestamptz NOT NULL DEFAULT now(),
        updated_at    timestamptz NOT NULL DEFAULT now(),
        lock_version  bigint NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE tenant_principals (
        id          uuid PRIMARY KEY,
        tenant_id   uuid NOT NULL REFERENCES tenants(id),
        kind        text NOT NULL CHECK (kind IN ('user','service')),
        user_id     uuid NULL REFERENCES users(id),
        service_key text NULL,
        status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
        created_at  timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, id),
        CONSTRAINT principal_kind_shape CHECK (
            (kind = 'user' AND user_id IS NOT NULL AND service_key IS NULL) OR
            (kind = 'service' AND user_id IS NULL AND service_key IS NOT NULL))
    )""",
    "CREATE UNIQUE INDEX tenant_principals_user_uq ON tenant_principals (tenant_id, user_id) WHERE user_id IS NOT NULL",
    "CREATE UNIQUE INDEX tenant_principals_service_uq ON tenant_principals (tenant_id, service_key) WHERE service_key IS NOT NULL",
    "CREATE INDEX tenant_principals_user_idx ON tenant_principals (user_id)",
    """CREATE TABLE memberships (
        id                 uuid PRIMARY KEY,
        tenant_id          uuid NOT NULL REFERENCES tenants(id),
        principal_id       uuid NOT NULL,
        role               text NOT NULL CHECK (role IN ('admin','bd','viewer')),
        joined_at          timestamptz NOT NULL DEFAULT now(),
        revoked_at         timestamptz NULL,
        permission_version bigint NOT NULL DEFAULT 1,
        UNIQUE (tenant_id, id),
        FOREIGN KEY (tenant_id, principal_id) REFERENCES tenant_principals (tenant_id, id)
    )""",
    "CREATE UNIQUE INDEX memberships_current_uq ON memberships (tenant_id, principal_id) WHERE revoked_at IS NULL",
    # ---------- 会话 ----------
    """CREATE TABLE sessions (
        id           uuid PRIMARY KEY,
        token_hash   text NOT NULL UNIQUE,
        csrf_hash    text NOT NULL,
        user_id      uuid NOT NULL REFERENCES users(id),
        tenant_id    uuid NULL REFERENCES tenants(id),
        principal_id uuid NULL,
        issued_at    timestamptz NOT NULL DEFAULT now(),
        last_seen_at timestamptz NOT NULL DEFAULT now(),
        expires_at   timestamptz NOT NULL,
        revoked_at   timestamptz NULL,
        FOREIGN KEY (tenant_id, principal_id) REFERENCES tenant_principals (tenant_id, id)
    )""",
    "CREATE INDEX sessions_user_idx ON sessions (user_id)",
    # ---------- 任务、尝试、outbox、幂等 ----------
    f"""CREATE TABLE jobs (
        id                     uuid PRIMARY KEY,
        tenant_id              uuid NOT NULL REFERENCES tenants(id),
        kind                   text NOT NULL,
        owner_principal_id     uuid NULL,
        requested_by           uuid NOT NULL,
        service_principal_id   uuid NULL,
        status                 text NOT NULL DEFAULT 'queued' CHECK (status IN ({JOB_STATUSES})),
        stage                  text NULL,
        payload_schema_version integer NOT NULL DEFAULT 1,
        params                 jsonb NOT NULL DEFAULT '{{}}',
        dedup_key              text NULL,
        scheduled_at           timestamptz NOT NULL DEFAULT now(),
        next_attempt_at        timestamptz NULL,
        lease_owner            text NULL,
        lease_expires_at       timestamptz NULL,
        fencing_token          bigint NOT NULL DEFAULT 0,
        cancel_requested_at    timestamptz NULL,
        progress               jsonb NULL,
        result                 jsonb NULL,
        last_error             jsonb NULL,
        created_at             timestamptz NOT NULL DEFAULT now(),
        updated_at             timestamptz NOT NULL DEFAULT now(),
        finished_at            timestamptz NULL,
        lock_version           bigint NOT NULL DEFAULT 1,
        UNIQUE (tenant_id, id),
        FOREIGN KEY (tenant_id, owner_principal_id) REFERENCES tenant_principals (tenant_id, id),
        FOREIGN KEY (tenant_id, requested_by) REFERENCES tenant_principals (tenant_id, id),
        FOREIGN KEY (tenant_id, service_principal_id) REFERENCES tenant_principals (tenant_id, id)
    )""",
    "CREATE UNIQUE INDEX jobs_dedup_uq ON jobs (tenant_id, kind, dedup_key) WHERE dedup_key IS NOT NULL",
    "CREATE INDEX jobs_owner_idx ON jobs (tenant_id, owner_principal_id, created_at DESC)",
    """CREATE TABLE job_attempts (
        id            uuid PRIMARY KEY,
        tenant_id     uuid NOT NULL REFERENCES tenants(id),
        job_id        uuid NOT NULL,
        attempt_no    integer NOT NULL,
        fencing_token bigint NOT NULL,
        worker_id     text NOT NULL,
        started_at    timestamptz NOT NULL DEFAULT now(),
        finished_at   timestamptz NULL,
        outcome       text NULL CHECK (outcome IN ('succeeded','failed','lease_lost','cancelled')),
        error_class   text NULL,
        UNIQUE (job_id, attempt_no),
        FOREIGN KEY (tenant_id, job_id) REFERENCES jobs (tenant_id, id)
    )""",
    """CREATE TABLE outbox_events (
        id                uuid PRIMARY KEY,
        tenant_id         uuid NOT NULL REFERENCES tenants(id),
        aggregate_type    text NOT NULL,
        aggregate_id      uuid NOT NULL,
        aggregate_version bigint NOT NULL,
        event_type        text NOT NULL,
        schema_version    integer NOT NULL DEFAULT 1,
        payload           jsonb NOT NULL DEFAULT '{}',
        occurred_at       timestamptz NOT NULL DEFAULT now(),
        published_at      timestamptz NULL,
        attempts          integer NOT NULL DEFAULT 0,
        last_error        text NULL,
        UNIQUE (tenant_id, aggregate_type, aggregate_id, aggregate_version, event_type)
    )""",
    "CREATE INDEX outbox_unpublished_idx ON outbox_events (occurred_at) WHERE published_at IS NULL",
    """CREATE TABLE idempotency_records (
        id           uuid PRIMARY KEY,
        tenant_id    uuid NOT NULL REFERENCES tenants(id),
        principal_id uuid NOT NULL,
        action       text NOT NULL,
        key          text NOT NULL CHECK (length(key) BETWEEN 8 AND 200),
        request_hash text NOT NULL,
        status       text NOT NULL CHECK (status IN ('in_progress','completed','failed')),
        resource_id  uuid NULL,
        response     jsonb NULL,
        created_at   timestamptz NOT NULL DEFAULT now(),
        expires_at   timestamptz NOT NULL,
        UNIQUE (tenant_id, principal_id, action, key),
        FOREIGN KEY (tenant_id, principal_id) REFERENCES tenant_principals (tenant_id, id)
    )""",
    # ---------- 权限：运行角色无 DELETE ----------
    "GRANT SELECT ON markets TO app_runtime",
    "GRANT SELECT, UPDATE ON users TO app_runtime",
    "GRANT SELECT, UPDATE ON password_credentials TO app_runtime",
    "GRANT SELECT, INSERT, UPDATE ON sessions TO app_runtime",
    "GRANT SELECT ON tenants, tenant_principals, memberships TO app_runtime",
    "GRANT SELECT, INSERT, UPDATE ON jobs, job_attempts, idempotency_records TO app_runtime",
    "GRANT INSERT ON outbox_events TO app_runtime",
    "GRANT SELECT, UPDATE ON outbox_events TO app_dispatcher",
]

RLS_TABLES = [
    "users",
    "password_credentials",
    "sessions",
    "tenants",
    "tenant_principals",
    "memberships",
    "jobs",
    "job_attempts",
    "outbox_events",
    "idempotency_records",
]

POLICIES: list[str] = [
    # 全局身份：只能看到自己，或登录流程中正在核对的那个账号
    """CREATE POLICY users_read ON users FOR SELECT TO app_runtime
         USING (id = app.current_user_id() OR provider_subject = app.login_subject())""",
    """CREATE POLICY users_update ON users FOR UPDATE TO app_runtime
         USING (id = app.current_user_id()) WITH CHECK (id = app.current_user_id())""",
    """CREATE POLICY credentials_read ON password_credentials FOR SELECT TO app_runtime
         USING (user_id = app.current_user_id()
                OR user_id IN (SELECT u.id FROM users u WHERE u.provider_subject = app.login_subject()))""",
    """CREATE POLICY credentials_update ON password_credentials FOR UPDATE TO app_runtime
         USING (user_id = app.current_user_id()) WITH CHECK (user_id = app.current_user_id())""",
    # 会话：凭 token 哈希或本人
    """CREATE POLICY sessions_read ON sessions FOR SELECT TO app_runtime
         USING (token_hash = app.session_token_hash() OR user_id = app.current_user_id())""",
    """CREATE POLICY sessions_insert ON sessions FOR INSERT TO app_runtime
         WITH CHECK (user_id = app.current_user_id())""",
    """CREATE POLICY sessions_update ON sessions FOR UPDATE TO app_runtime
         USING (token_hash = app.session_token_hash() OR user_id = app.current_user_id())
         WITH CHECK (user_id = app.current_user_id() OR token_hash = app.session_token_hash())""",
    # 企业：当前企业，或本人所属的企业（用于登录后选择企业）
    """CREATE POLICY principals_read ON tenant_principals FOR SELECT TO app_runtime
         USING (tenant_id = app.current_tenant() OR user_id = app.current_user_id())""",
    """CREATE POLICY tenants_read ON tenants FOR SELECT TO app_runtime
         USING (id = app.current_tenant()
                OR id IN (SELECT p.tenant_id FROM tenant_principals p WHERE p.user_id = app.current_user_id()))""",
    """CREATE POLICY memberships_read ON memberships FOR SELECT TO app_runtime
         USING (tenant_id = app.current_tenant()
                OR principal_id IN (SELECT p.id FROM tenant_principals p WHERE p.user_id = app.current_user_id()))""",
    # 任务：租户门禁（RESTRICTIVE）+ 本人可见；Worker 通过 app.claim_job_id 领取指定任务
    """CREATE POLICY jobs_tenant_guard ON jobs AS RESTRICTIVE FOR ALL TO app_runtime
         USING (tenant_id = app.current_tenant() OR id = app.claim_job_id())
         WITH CHECK (tenant_id = app.current_tenant())""",
    """CREATE POLICY jobs_read ON jobs FOR SELECT TO app_runtime
         USING (owner_principal_id = app.current_principal() OR requested_by = app.current_principal()
                OR id = app.claim_job_id())""",
    """CREATE POLICY jobs_insert ON jobs FOR INSERT TO app_runtime
         WITH CHECK (requested_by = app.current_principal()
                     AND (owner_principal_id IS NULL OR owner_principal_id = app.current_principal()))""",
    """CREATE POLICY jobs_update ON jobs FOR UPDATE TO app_runtime
         USING (owner_principal_id = app.current_principal() OR requested_by = app.current_principal()
                OR id = app.claim_job_id())
         WITH CHECK (true)""",
    """CREATE POLICY job_attempts_tenant_guard ON job_attempts AS RESTRICTIVE FOR ALL TO app_runtime
         USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant())""",
    """CREATE POLICY job_attempts_rw ON job_attempts FOR ALL TO app_runtime
         USING (job_id IN (SELECT j.id FROM jobs j)) WITH CHECK (job_id IN (SELECT j.id FROM jobs j))""",
    # outbox：运行角色只能写本企业；分发角色可跨企业读取（负载只含 job_id 等最小信息）
    """CREATE POLICY outbox_tenant_guard ON outbox_events AS RESTRICTIVE FOR ALL TO app_runtime
         USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant())""",
    "CREATE POLICY outbox_insert ON outbox_events FOR INSERT TO app_runtime WITH CHECK (true)",
    "CREATE POLICY outbox_dispatch_read ON outbox_events FOR SELECT TO app_dispatcher USING (true)",
    "CREATE POLICY outbox_dispatch_update ON outbox_events FOR UPDATE TO app_dispatcher USING (true) WITH CHECK (true)",
    # 幂等记录：本企业 + 本人
    """CREATE POLICY idem_tenant_guard ON idempotency_records AS RESTRICTIVE FOR ALL TO app_runtime
         USING (tenant_id = app.current_tenant()) WITH CHECK (tenant_id = app.current_tenant())""",
    """CREATE POLICY idem_rw ON idempotency_records FOR ALL TO app_runtime
         USING (principal_id = app.current_principal()) WITH CHECK (principal_id = app.current_principal())""",
]

# 欧洲站点目录（2026-09-29 用 FastMoss MCP creator_search 实测，见 docs/纵向切片方案.md 3.1）
MARKETS = [
    # code, 中文, 英文, 币种, 时区, 语言, fastmoss_region, data_status, total, probe_currency, 备注, 排序
    (
        "UK",
        "英国",
        "United Kingdom",
        "GBP",
        "Europe/London",
        "{en}",
        "GB",
        "queryable",
        2000,
        "GBP",
        "FastMoss 地区码必须用 GB；传 UK 返回 0 条且不报错",
        10,
    ),
    ("DE", "德国", "Germany", "EUR", "Europe/Berlin", "{de}", "DE", "queryable", 2000, "EUR", "", 20),
    ("FR", "法国", "France", "EUR", "Europe/Paris", "{fr}", "FR", "queryable", 2000, "EUR", "", 30),
    ("IT", "意大利", "Italy", "EUR", "Europe/Rome", "{it}", "IT", "queryable", 2000, "EUR", "", 40),
    ("ES", "西班牙", "Spain", "EUR", "Europe/Madrid", "{es}", "ES", "queryable", 2000, "EUR", "", 50),
    (
        "NL",
        "荷兰",
        "Netherlands",
        "EUR",
        "Europe/Amsterdam",
        "{nl}",
        "NL",
        "queryable_currency_unknown",
        2000,
        None,
        "返回金额的 currency 为空",
        60,
    ),
    (
        "BE",
        "比利时",
        "Belgium",
        "EUR",
        "Europe/Brussels",
        "{nl,fr}",
        "BE",
        "queryable_currency_unknown",
        1252,
        None,
        "返回金额的 currency 为空",
        70,
    ),
    (
        "AT",
        "奥地利",
        "Austria",
        "EUR",
        "Europe/Vienna",
        "{de}",
        "AT",
        "queryable_currency_unknown",
        775,
        None,
        "返回金额的 currency 为空",
        80,
    ),
    (
        "PL",
        "波兰",
        "Poland",
        "PLN",
        "Europe/Warsaw",
        "{pl}",
        "PL",
        "queryable_currency_unknown",
        2000,
        None,
        "返回金额的 currency 为空",
        90,
    ),
    (
        "PT",
        "葡萄牙",
        "Portugal",
        "EUR",
        "Europe/Lisbon",
        "{pt}",
        "PT",
        "platform_unconfirmed",
        2000,
        None,
        "FastMoss 有数据；TikTok Shop 是否已开放待确认",
        100,
    ),
    (
        "IE",
        "爱尔兰",
        "Ireland",
        "EUR",
        "Europe/Dublin",
        "{en}",
        None,
        "manual_import_only",
        0,
        None,
        "FastMoss 无 IE 数据，只能人工导入",
        110,
    ),
]


def _q(v: object) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, int):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def upgrade() -> None:
    for sql in STATEMENTS:
        op.execute(sql)
    for t in RLS_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
    for sql in POLICIES:
        op.execute(sql)
    for m in MARKETS:
        code, zh, en, cur, tz, langs, region, status, total, pcur, notes, order = m
        # 值来自本文件常量 MARKETS，经 _q 转义
        op.execute(
            "INSERT INTO markets (market_code, name_zh, name_en, region_group, settlement_currency, default_time_zone,"  # noqa: S608
            " content_languages, fastmoss_region, data_status, last_probe_at, last_probe_total, probe_currency,"
            f" notes, sort_order) VALUES ({_q(code)}, {_q(zh)}, {_q(en)}, 'europe', {_q(cur)}, {_q(tz)},"
            f" {_q(langs)}, {_q(region)}, {_q(status)}, TIMESTAMPTZ '2026-09-29 10:40:00+08', {_q(total)},"
            f" {_q(pcur)}, {_q(notes)}, {_q(order)})"
        )


def downgrade() -> None:
    for t in reversed(RLS_TABLES + ["markets"]):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("DROP SCHEMA IF EXISTS app CASCADE")
