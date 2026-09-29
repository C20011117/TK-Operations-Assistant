"""桌面版初始结构（SQLite）：设置、市场目录（欧洲 11 站）、任务、任务尝试、幂等记录。

Revision ID: 0001_desktop_foundation
Revises:
Create Date: 2026-09-29
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0001_desktop_foundation"
down_revision = None
branch_labels = None
depends_on = None

JOB_STATUSES = (
    "queued",
    "running",
    "waiting_input",
    "cancel_requested",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
)

MARKETS = [
    # code, 中文, 英文, 币种, 时区, 语言, fastmoss_region, data_status, total, probe_currency, 备注, 排序
    (
        "UK",
        "英国",
        "United Kingdom",
        "GBP",
        "Europe/London",
        '["en"]',
        "GB",
        "queryable",
        2000,
        "GBP",
        "FastMoss 地区码必须用 GB；传 UK 返回 0 条且不报错",
        10,
    ),
    ("DE", "德国", "Germany", "EUR", "Europe/Berlin", '["de"]', "DE", "queryable", 2000, "EUR", "", 20),
    ("FR", "法国", "France", "EUR", "Europe/Paris", '["fr"]', "FR", "queryable", 2000, "EUR", "", 30),
    ("IT", "意大利", "Italy", "EUR", "Europe/Rome", '["it"]', "IT", "queryable", 2000, "EUR", "", 40),
    ("ES", "西班牙", "Spain", "EUR", "Europe/Madrid", '["es"]', "ES", "queryable", 2000, "EUR", "", 50),
    (
        "NL",
        "荷兰",
        "Netherlands",
        "EUR",
        "Europe/Amsterdam",
        '["nl"]',
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
        '["nl", "fr"]',
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
        '["de"]',
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
        '["pl"]',
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
        '["pt"]',
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
        '["en"]',
        None,
        "manual_import_only",
        0,
        None,
        "FastMoss 无 IE 数据，只能人工导入",
        110,
    ),
]


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
    )
    op.create_table(
        "markets",
        sa.Column("market_code", sa.String(2), primary_key=True),
        sa.Column("name_zh", sa.Text, nullable=False),
        sa.Column("name_en", sa.Text, nullable=False),
        sa.Column("region_group", sa.String(16), nullable=False),
        sa.Column("settlement_currency", sa.String(3), nullable=False),
        sa.Column("default_time_zone", sa.Text, nullable=False),
        sa.Column("content_languages", sa.Text, nullable=False),  # JSON 数组
        sa.Column("fastmoss_region", sa.String(8), nullable=True),
        sa.Column("data_status", sa.String(32), nullable=False),
        sa.Column("last_probe_at", sa.String(32), nullable=True),
        sa.Column("last_probe_total", sa.Integer, nullable=True),
        sa.Column("probe_currency", sa.String(3), nullable=True),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.CheckConstraint("region_group IN ('europe','americas','asia')", name="ck_markets_region_group"),
        sa.CheckConstraint(
            "data_status IN ('queryable','queryable_currency_unknown','platform_unconfirmed','manual_import_only')",
            name="ck_markets_data_status",
        ),
        sa.CheckConstraint(
            "(data_status = 'manual_import_only') = (fastmoss_region IS NULL)",
            name="ck_markets_manual_no_region",
        ),
    )
    statuses = ",".join(f"'{s}'" for s in JOB_STATUSES)
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("queue", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(64), nullable=True),
        sa.Column("params", sa.Text, nullable=False, server_default="{}"),
        sa.Column("progress", sa.Text, nullable=True),
        sa.Column("result", sa.Text, nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("fencing_token", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attempt", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.String(32), nullable=True),
        sa.Column("cancel_requested", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.Column("started_at", sa.String(32), nullable=True),
        sa.Column("finished_at", sa.String(32), nullable=True),
        sa.CheckConstraint(f"status IN ({statuses})", name="ck_jobs_status"),
    )
    op.create_index("ix_jobs_status_created", "jobs", ["status", "created_at"])
    op.create_table(
        "job_attempts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_no", sa.Integer, nullable=False),
        sa.Column("fencing_token", sa.Integer, nullable=False),
        sa.Column("worker_id", sa.String(64), nullable=False),
        sa.Column("started_at", sa.String(32), nullable=False),
        sa.Column("finished_at", sa.String(32), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("error_class", sa.String(128), nullable=True),
        sa.UniqueConstraint("job_id", "attempt_no", name="uq_job_attempts_no"),
    )
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.UniqueConstraint("action", "key", name="uq_idempotency_action_key"),
    )

    markets = sa.table(
        "markets",
        *(
            sa.column(c)
            for c in (
                "market_code",
                "name_zh",
                "name_en",
                "region_group",
                "settlement_currency",
                "default_time_zone",
                "content_languages",
                "fastmoss_region",
                "data_status",
                "last_probe_at",
                "last_probe_total",
                "probe_currency",
                "notes",
                "sort_order",
            )
        ),
    )
    op.bulk_insert(
        markets,
        [
            {
                "market_code": code,
                "name_zh": zh,
                "name_en": en,
                "region_group": "europe",
                "settlement_currency": cur,
                "default_time_zone": tz,
                "content_languages": json.dumps(json.loads(langs)),
                "fastmoss_region": region,
                "data_status": status,
                "last_probe_at": "2026-09-29T02:40:00.000Z",
                "last_probe_total": total,
                "probe_currency": pcur,
                "notes": notes,
                "sort_order": order,
            }
            for code, zh, en, cur, tz, langs, region, status, total, pcur, notes, order in MARKETS
        ],
    )


def downgrade() -> None:
    for t in ("idempotency_records", "job_attempts", "jobs", "markets", "app_settings"):
        op.drop_table(t)
