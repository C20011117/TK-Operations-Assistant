"""M1：产品档案（版本 + 各站点价格条款）与找人任务（单站点执行单元 + 条件版本）。

Revision ID: 0002_products_campaigns
Revises: 0001_desktop_foundation
Create Date: 2026-09-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_products_campaigns"
down_revision = "0001_desktop_foundation"
branch_labels = None
depends_on = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("sku", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        # 指向当前已确认版本；不加外键，避免与 product_versions 循环依赖，由服务层维护
        sa.Column("current_version_id", sa.String(36), nullable=True),
        *_ts(),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_products_status"),
    )
    op.create_index(
        "uq_products_active_sku", "products", ["sku"], unique=True, sqlite_where=sa.text("status = 'active'")
    )

    op.create_table(
        "product_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "product_id", sa.String(36), sa.ForeignKey("products.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("facts", sa.Text, nullable=False),  # JSON：卖点、场景、禁用表述等
        sa.Column("facts_schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("confirmed_at", sa.String(32), nullable=True),
        *_ts(),
        sa.CheckConstraint("version_no > 0", name="ck_product_versions_no"),
        sa.CheckConstraint("status IN ('draft','confirmed','superseded')", name="ck_product_versions_status"),
        sa.CheckConstraint(
            "(status = 'draft') = (confirmed_at IS NULL)", name="ck_product_versions_confirmed_at"
        ),
        sa.UniqueConstraint("product_id", "version_no", name="uq_product_versions_no"),
    )
    # 每个产品最多一个草稿、一个当前确认版本
    op.create_index(
        "uq_product_versions_one_draft",
        "product_versions",
        ["product_id"],
        unique=True,
        sqlite_where=sa.text("status = 'draft'"),
    )
    op.create_index(
        "uq_product_versions_one_confirmed",
        "product_versions",
        ["product_id"],
        unique=True,
        sqlite_where=sa.text("status = 'confirmed'"),
    )

    op.create_table(
        "product_market_terms",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "product_version_id",
            sa.String(36),
            sa.ForeignKey("product_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("market_code", sa.String(2), sa.ForeignKey("markets.market_code"), nullable=False),
        sa.Column("price_status", sa.String(16), nullable=False),
        sa.Column("price_amount", sa.String(40), nullable=True),  # 十进制字符串
        sa.Column("price_currency", sa.String(3), nullable=False),
        sa.Column("sample_policy", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("commission_min_pct", sa.String(40), nullable=True),
        sa.Column("commission_max_pct", sa.String(40), nullable=True),
        sa.Column("quote_valid_until", sa.String(10), nullable=True),  # YYYY-MM-DD
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.CheckConstraint("price_status IN ('known','unknown')", name="ck_pmt_price_status"),
        sa.CheckConstraint(
            "(price_status = 'known') = (price_amount IS NOT NULL)", name="ck_pmt_price_amount"
        ),
        sa.CheckConstraint("sample_policy IN ('free','paid','none','unknown')", name="ck_pmt_sample_policy"),
        sa.UniqueConstraint("product_version_id", "market_code", name="uq_pmt_version_market"),
    )

    op.create_table(
        "campaigns",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("product_id", sa.String(36), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("goal", sa.String(16), nullable=False),
        sa.Column("collaboration_type", sa.String(16), nullable=False),
        sa.Column("start_date", sa.String(10), nullable=True),
        sa.Column("end_date", sa.String(10), nullable=True),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        *_ts(),
        sa.CheckConstraint("goal IN ('sales','content','awareness')", name="ck_campaigns_goal"),
        sa.CheckConstraint(
            "collaboration_type IN ('free_sample','paid','commission','hybrid')", name="ck_campaigns_collab"
        ),
        sa.CheckConstraint("status IN ('active','archived')", name="ck_campaigns_status"),
        sa.CheckConstraint(
            "start_date IS NULL OR end_date IS NULL OR end_date >= start_date", name="ck_campaigns_dates"
        ),
    )

    op.create_table(
        "campaign_markets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "campaign_id", sa.String(36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("market_code", sa.String(2), sa.ForeignKey("markets.market_code"), nullable=False),
        sa.Column("time_zone", sa.Text, nullable=False),
        sa.Column("reporting_currency", sa.String(3), nullable=False),
        sa.Column("product_version_id", sa.String(36), sa.ForeignKey("product_versions.id"), nullable=False),
        sa.Column("target_list_size", sa.Integer, nullable=True),
        sa.Column("budget_min", sa.String(40), nullable=True),
        sa.Column("budget_max", sa.String(40), nullable=True),
        sa.Column("cost_cap_credits", sa.Integer, nullable=True),
        sa.Column("current_criteria_version_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("confirmed_at", sa.String(32), nullable=True),
        *_ts(),
        sa.CheckConstraint("status IN ('draft','ready')", name="ck_cm_status"),
        sa.CheckConstraint(
            "target_list_size IS NULL OR target_list_size BETWEEN 1 AND 500", name="ck_cm_target"
        ),
        sa.CheckConstraint("cost_cap_credits IS NULL OR cost_cap_credits >= 0", name="ck_cm_cost_cap"),
        sa.UniqueConstraint("campaign_id", "market_code", name="uq_cm_campaign_market"),
    )

    op.create_table(
        "criteria_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "campaign_market_id",
            sa.String(36),
            sa.ForeignKey("campaign_markets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("product_version_id", sa.String(36), sa.ForeignKey("product_versions.id"), nullable=False),
        sa.Column("criteria", sa.Text, nullable=False),  # JSON：受限 DSL 条件列表
        sa.Column("search", sa.Text, nullable=False),  # JSON：搜索关键词等
        sa.Column("schema_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("confirmed_at", sa.String(32), nullable=True),
        *_ts(),
        sa.CheckConstraint("status IN ('draft','confirmed','superseded')", name="ck_cv_status"),
        sa.CheckConstraint("(status = 'draft') = (confirmed_at IS NULL)", name="ck_cv_confirmed_at"),
        sa.UniqueConstraint("campaign_market_id", "version_no", name="uq_cv_no"),
    )
    op.create_index(
        "uq_cv_one_draft",
        "criteria_versions",
        ["campaign_market_id"],
        unique=True,
        sqlite_where=sa.text("status = 'draft'"),
    )
    op.create_index(
        "uq_cv_one_confirmed",
        "criteria_versions",
        ["campaign_market_id"],
        unique=True,
        sqlite_where=sa.text("status = 'confirmed'"),
    )
    op.create_index("ix_campaigns_product", "campaigns", ["product_id"])
    op.create_index("ix_cm_status_updated", "campaign_markets", ["status", "updated_at"])


def downgrade() -> None:
    for t in (
        "criteria_versions",
        "campaign_markets",
        "campaigns",
        "product_market_terms",
        "product_versions",
        "products",
    ):
        op.drop_table(t)
