"""M3.1：邀约话术草稿、跟进提醒。

- outreach_drafts：AI 生成的邀约 / 跟进话术（目标语言 + 中文对照）。只含公开数据，不含收件信息。
- collaborations.follow_up_at：手动指定的下次跟进时间（“稍后提醒”/“N 天后再跟进”）；为空时按规则计算。

Revision ID: 0005_outreach_followup
Revises: 0004_collaboration
Create Date: 2026-09-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_outreach_followup"
down_revision = "0004_collaboration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("collaborations", sa.Column("follow_up_at", sa.String(32), nullable=True))
    op.create_table(
        "outreach_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("collaboration_id", sa.String(36), sa.ForeignKey("collaborations.id"), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),  # invite | follow_up
        sa.Column("channel", sa.String(24), nullable=False),  # tiktok_message | email
        sa.Column("language", sa.String(8), nullable=False),
        sa.Column(
            "content", sa.Text, nullable=False
        ),  # JSON：subject / message / message_zh / notes / warnings
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("sent_at", sa.String(32), nullable=True),
        sa.Column("created_at", sa.String(32), nullable=False),
    )
    op.create_index("ix_outreach_collab", "outreach_drafts", ["collaboration_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_outreach_collab", "outreach_drafts")
    op.drop_table("outreach_drafts")
    with op.batch_alter_table("collaborations") as b:
        b.drop_column("follow_up_at")
