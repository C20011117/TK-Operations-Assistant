"""M2：匹配运行、FastMoss 调用记录与规范化快照、达人、候选评估、证据、推荐快照、费用流水。

合规：不保存 FastMoss 原始响应，只保存规范化后的快照（字段白名单）。

Revision ID: 0003_matching
Revises: 0002_products_campaigns
Create Date: 2026-09-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_matching"
down_revision = "0002_products_campaigns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "matching_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_market_id", sa.String(36), sa.ForeignKey("campaign_markets.id"), nullable=False),
        sa.Column(
            "criteria_version_id", sa.String(36), sa.ForeignKey("criteria_versions.id"), nullable=False
        ),
        sa.Column("product_version_id", sa.String(36), sa.ForeignKey("product_versions.id"), nullable=False),
        sa.Column("market_code", sa.String(2), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),  # fastmoss | manual_import
        sa.Column("provider_region", sa.String(8), nullable=True),  # 实际传给 FastMoss 的地区码
        sa.Column("reporting_currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(32), nullable=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("target_list_size", sa.Integer, nullable=False),
        sa.Column("cost_cap_credits", sa.Integer, nullable=True),
        sa.Column("credits_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("llm_input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("llm_output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("counters", sa.Text, nullable=False, server_default="{}"),  # JSON
        sa.Column("stop_reason", sa.String(40), nullable=True),
        sa.Column("error", sa.Text, nullable=True),  # JSON {code, message}
        sa.Column("versions", sa.Text, nullable=False, server_default="{}"),  # graph/prompt/ranking 版本
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("started_at", sa.String(32), nullable=True),
        sa.Column("finished_at", sa.String(32), nullable=True),
        sa.Column("updated_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_runs_cm_created", "matching_runs", ["campaign_market_id", "created_at"])

    op.create_table(
        "provider_calls",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("matching_runs.id"), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("transport", sa.String(16), nullable=False),  # mcp | manual | fixture
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("params", sa.Text, nullable=False),  # JSON，不含任何密钥
        # pending | succeeded | empty | failed | insufficient_credits | rate_limited | unauthorized | unknown
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("provider_request_id", sa.String(128), nullable=True),
        sa.Column("charged", sa.Integer, nullable=True),
        sa.Column("credit_cost", sa.Integer, nullable=True),
        sa.Column("remaining_credits", sa.Integer, nullable=True),
        sa.Column("result_count", sa.Integer, nullable=True),
        sa.Column("total", sa.Integer, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("finished_at", sa.String(32), nullable=True),
        sa.UniqueConstraint("run_id", "request_hash", name="uq_provider_call_request"),
    )

    op.create_table(
        "provider_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "provider_call_id", sa.String(36), sa.ForeignKey("provider_calls.id"), nullable=False, unique=True
        ),
        sa.Column("schema_version", sa.Integer, nullable=False),
        sa.Column("records", sa.Text, nullable=False),  # JSON：规范化后的达人记录列表
        sa.Column("created_at", sa.String(32), nullable=False),
    )

    op.create_table(
        "creators",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("platform", sa.String(16), nullable=False, server_default="tiktok"),
        sa.Column("provider_uid", sa.String(64), nullable=True),
        sa.Column("unique_id", sa.String(128), nullable=True),  # TikTok 用户名（公开）
        sa.Column("nickname", sa.Text, nullable=True),
        sa.Column("region", sa.String(8), nullable=True),
        sa.Column("first_seen_at", sa.String(32), nullable=False),
        sa.Column("last_seen_at", sa.String(32), nullable=False),
    )
    op.create_index("uq_creators_uid", "creators", ["platform", "provider_uid"], unique=True)
    op.create_index("ix_creators_unique_id", "creators", ["platform", "unique_id"])

    op.create_table(
        "candidate_evaluations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("matching_runs.id"), nullable=False),
        sa.Column("creator_id", sa.String(36), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column("discovery", sa.Text, nullable=False),  # JSON：发现路径（关键词、页码、调用 ID）
        sa.Column("observations", sa.Text, nullable=False),  # JSON：规范化指标 + 状态
        sa.Column("rule_results", sa.Text, nullable=False),  # JSON：每条条件 pass/fail/unknown
        sa.Column("hard_status", sa.String(16), nullable=False),  # pass | fail | unknown
        sa.Column("group_key", sa.String(24), nullable=False),  # qualified | needs_verification | excluded
        sa.Column("soft_score", sa.Integer, nullable=True),
        sa.Column("assessment", sa.Text, nullable=True),  # JSON：经过校验的 AI 判断
        # pending | ok | failed | skipped | not_needed
        sa.Column("assessment_status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.String(32), nullable=False),
        sa.Column("updated_at", sa.String(32), nullable=False),
        sa.UniqueConstraint("run_id", "creator_id", name="uq_eval_run_creator"),
    )

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("matching_runs.id"), nullable=False),
        sa.Column("creator_id", sa.String(36), sa.ForeignKey("creators.id"), nullable=True),
        sa.Column("provider_call_id", sa.String(36), sa.ForeignKey("provider_calls.id"), nullable=True),
        sa.Column("kind", sa.String(24), nullable=False),  # metric | profile_text | product_fact
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.Text, nullable=True),
        sa.Column("currency", sa.String(3), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),  # known | unknown | anomaly | inconsistent
        sa.Column("locator", sa.Text, nullable=False),
        sa.Column("fetched_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_evidence_run_creator", "evidence_items", ["run_id", "creator_id"])

    op.create_table(
        "recommendation_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("matching_runs.id"), nullable=False, unique=True),
        sa.Column("counts", sa.Text, nullable=False),
        sa.Column("versions", sa.Text, nullable=False),
        sa.Column("limitations", sa.Text, nullable=False),  # JSON 数组：本次结果的已知限制
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_table(
        "recommendation_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("snapshot_id", sa.String(36), sa.ForeignKey("recommendation_snapshots.id"), nullable=False),
        sa.Column("evaluation_id", sa.String(36), sa.ForeignKey("candidate_evaluations.id"), nullable=False),
        sa.Column("group_key", sa.String(24), nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("card", sa.Text, nullable=False),  # JSON：冻结的推荐卡内容
        sa.UniqueConstraint("snapshot_id", "evaluation_id", name="uq_rec_item"),
    )

    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("matching_runs.id"), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),  # fastmoss | llm
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("provider_call_id", sa.String(36), sa.ForeignKey("provider_calls.id"), nullable=True),
        sa.Column("credits", sa.Integer, nullable=True),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_usage_created", "usage_ledger", ["created_at"])


def downgrade() -> None:
    for t in (
        "usage_ledger",
        "recommendation_items",
        "recommendation_snapshots",
        "evidence_items",
        "candidate_evaluations",
        "creators",
        "provider_snapshots",
        "provider_calls",
        "matching_runs",
    ):
        op.drop_table(t)
