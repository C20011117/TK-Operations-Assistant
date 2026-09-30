"""M4：拍摄包与视频审核。

- content_briefs / content_brief_versions：拍摄包及其不可修改的版本（目标语言 + 中文对照）。
- production_rounds：拍摄轮次（1 轮 = 1 条新视频），确认时锁定拍摄包版本。
- video_assets / video_versions：视频文件（按 SHA-256 去重）与每轮的 V1、V2…
- feedback_items / video_reviews：时间码反馈与审核结论。

Revision ID: 0006_production
Revises: 0005_outreach_followup
Create Date: 2026-09-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_production"
down_revision = "0005_outreach_followup"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column("id", sa.String(36), primary_key=True)


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.String(32), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "content_briefs",
        _id(),
        sa.Column(
            "collaboration_id", sa.String(36), sa.ForeignKey("collaborations.id"), nullable=False, unique=True
        ),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_table(
        "content_brief_versions",
        _id(),
        sa.Column("brief_id", sa.String(36), sa.ForeignKey("content_briefs.id"), nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("content_language", sa.String(8), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("body_zh", sa.Text, nullable=False, server_default=""),
        sa.Column("cautions", sa.Text, nullable=False, server_default="[]"),  # JSON 列表
        sa.Column("source", sa.String(8), nullable=False),  # ai | manual
        sa.Column(
            "based_on_version_id", sa.String(36), sa.ForeignKey("content_brief_versions.id"), nullable=True
        ),
        sa.Column("product_version_id", sa.String(36), sa.ForeignKey("product_versions.id"), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("brief_id", "version_no", name="uq_brief_version_no"),
        sa.CheckConstraint("source IN ('ai','manual')", name="ck_brief_source"),
    )
    op.create_table(
        "production_rounds",
        _id(),
        sa.Column("collaboration_id", sa.String(36), sa.ForeignKey("collaborations.id"), nullable=False),
        sa.Column("round_no", sa.Integer, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "brief_version_id", sa.String(36), sa.ForeignKey("content_brief_versions.id"), nullable=True
        ),
        _ts("brief_confirmed_at", True),
        sa.Column("accepted_version_id", sa.String(36), nullable=True),
        _ts("accepted_at", True),
        _ts("cancelled_at", True),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("collaboration_id", "round_no", name="uq_round_no"),
        sa.CheckConstraint(
            "status IN ('briefing','awaiting_video','in_review','revision_requested','accepted','cancelled')",
            name="ck_round_status",
        ),
    )
    op.create_table(
        "video_assets",
        _id(),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("rel_path", sa.String(255), nullable=False),
        _ts("created_at"),
    )
    op.create_table(
        "video_versions",
        _id(),
        sa.Column("round_id", sa.String(36), sa.ForeignKey("production_rounds.id"), nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("asset_id", sa.String(36), sa.ForeignKey("video_assets.id"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(24), nullable=False),
        _ts("created_at"),
        sa.UniqueConstraint("round_id", "sha256", name="uq_round_sha256"),
        sa.UniqueConstraint("round_id", "version_no", name="uq_round_video_no"),
        sa.CheckConstraint("status IN ('in_review','changes_requested','accepted')", name="ck_video_status"),
    )
    op.create_table(
        "video_reviews",
        _id(),
        sa.Column("video_version_id", sa.String(36), sa.ForeignKey("video_versions.id"), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("summary", sa.Text, nullable=False, server_default=""),
        sa.Column("message", sa.Text, nullable=True),  # 发给达人的反馈消息（目标语言），可空
        sa.Column("message_language", sa.String(8), nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("video_version_id", name="uq_review_per_version"),
        sa.CheckConstraint("decision IN ('changes_requested','accepted')", name="ck_review_decision"),
    )
    op.create_table(
        "feedback_items",
        _id(),
        sa.Column("video_version_id", sa.String(36), sa.ForeignKey("video_versions.id"), nullable=False),
        sa.Column("review_id", sa.String(36), sa.ForeignKey("video_reviews.id"), nullable=True),
        sa.Column("timecode_ms", sa.Integer, nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(8), nullable=False),  # must | should
        sa.Column("body", sa.Text, nullable=False),
        _ts("created_at"),
        sa.CheckConstraint("timecode_ms >= 0", name="ck_feedback_timecode"),
    )
    op.create_index("ix_round_collab", "production_rounds", ["collaboration_id", "round_no"])
    op.create_index("ix_feedback_version", "feedback_items", ["video_version_id", "timecode_ms"])


def downgrade() -> None:
    op.drop_index("ix_feedback_version", "feedback_items")
    op.drop_index("ix_round_collab", "production_rounds")
    for t in (
        "feedback_items",
        "video_reviews",
        "video_versions",
        "video_assets",
        "production_rounds",
        "content_brief_versions",
        "content_briefs",
    ):
        op.drop_table(t)
