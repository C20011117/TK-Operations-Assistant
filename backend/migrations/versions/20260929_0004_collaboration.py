"""M3：候选决定、达人关系、合作、寄样（本人确认 + 幂等）、对外动作与确认。

个人数据：收件信息、快递单号只存密文列（*_enc，AES-GCM，数据密钥在 Windows 凭据管理器）。

Revision ID: 0004_collaboration
Revises: 0003_matching
Create Date: 2026-09-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_collaboration"
down_revision = "0003_matching"
branch_labels = None
depends_on = None


def _id() -> sa.Column:
    return sa.Column("id", sa.String(36), primary_key=True)


def _ts(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.String(32), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "creator_decisions",
        _id(),
        sa.Column("campaign_market_id", sa.String(36), sa.ForeignKey("campaign_markets.id"), nullable=False),
        sa.Column("creator_id", sa.String(36), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column(
            "decision", sa.String(24), nullable=False
        ),  # keep | needs_verification | exclude | reconsider
        sa.Column("reason_code", sa.String(32), nullable=True),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("matching_runs.id"), nullable=True),
        sa.Column("evaluation_id", sa.String(36), sa.ForeignKey("candidate_evaluations.id"), nullable=True),
        sa.Column("supersedes_id", sa.String(36), sa.ForeignKey("creator_decisions.id"), nullable=True),
        _ts("created_at"),
    )
    op.create_index(
        "ix_decisions_cm_creator", "creator_decisions", ["campaign_market_id", "creator_id", "created_at"]
    )

    op.create_table(
        "creator_relationships",
        _id(),
        sa.Column("creator_id", sa.String(36), sa.ForeignKey("creators.id"), nullable=False, unique=True),
        sa.Column(
            "status", sa.String(16), nullable=False, server_default="active"
        ),  # active | paused | archived
        sa.Column("preferred_language", sa.String(8), nullable=True),
        sa.Column("notes", sa.Text, nullable=False, server_default=""),
        _ts("first_contact_at", True),
        _ts("last_interaction_at", True),
        _ts("created_at"),
        _ts("updated_at"),
    )

    op.create_table(
        "collaborations",
        _id(),
        sa.Column(
            "relationship_id", sa.String(36), sa.ForeignKey("creator_relationships.id"), nullable=False
        ),
        sa.Column("campaign_market_id", sa.String(36), sa.ForeignKey("campaign_markets.id"), nullable=False),
        sa.Column("product_version_id", sa.String(36), sa.ForeignKey("product_versions.id"), nullable=False),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("matching_runs.id"), nullable=True),
        sa.Column("evaluation_id", sa.String(36), sa.ForeignKey("candidate_evaluations.id"), nullable=True),
        # planned | contacting | negotiating | agreed | in_progress | completed | closed
        sa.Column("status", sa.String(16), nullable=False, server_default="planned"),
        sa.Column("closed_reason", sa.String(24), nullable=True),
        sa.Column("agreement", sa.Text, nullable=True),  # JSON：双方如何、何时达成，约定条款摘要
        sa.Column("agreed_video_count", sa.Integer, nullable=True),
        _ts("agreed_at", True),
        _ts("started_at", True),
        _ts("closed_at", True),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("relationship_id", "campaign_market_id", name="uq_collab_relationship_cm"),
    )
    op.create_index("ix_collab_status", "collaborations", ["status", "updated_at"])

    op.create_table(
        "collaboration_events",
        _id(),
        sa.Column("collaboration_id", sa.String(36), sa.ForeignKey("collaborations.id"), nullable=False),
        sa.Column(
            "kind", sa.String(32), nullable=False
        ),  # created | transition | agreement | shipment | closed
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=True),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("ref_id", sa.String(36), nullable=True),
        _ts("created_at"),
    )
    op.create_index("ix_collab_events", "collaboration_events", ["collaboration_id", "created_at"])

    op.create_table(
        "sample_shipments",
        _id(),
        sa.Column("collaboration_id", sa.String(36), sa.ForeignKey("collaborations.id"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),  # initial_sample | replacement | additional_sample
        # draft | awaiting_confirmation | confirmed | dispatched | cancelled
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        # unknown | in_transit | delivered | exception | returned（由物流节点投影；没有节点就是 unknown）
        sa.Column("delivery_status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("items", sa.Text, nullable=False),  # JSON：[{sku, variant, quantity, unit_cost, currency}]
        sa.Column("cost_cap_amount", sa.String(40), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("recipient_enc", sa.Text, nullable=False),  # 密文：姓名、电话、邮箱、地址
        sa.Column("recipient_country", sa.String(2), nullable=True),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column("payload_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("carrier", sa.String(64), nullable=True),
        sa.Column("tracking_enc", sa.Text, nullable=True),  # 密文：快递单号
        _ts("dispatched_at", True),
        _ts("delivered_at", True),
        sa.Column("external_action_id", sa.String(36), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_index("ix_shipments_collab", "sample_shipments", ["collaboration_id", "created_at"])

    op.create_table(
        "shipment_events",
        _id(),
        sa.Column("shipment_id", sa.String(36), sa.ForeignKey("sample_shipments.id"), nullable=False),
        sa.Column("source", sa.String(16), nullable=False, server_default="manual"),  # manual | carrier
        sa.Column("status", sa.String(16), nullable=False),  # in_transit | delivered | exception | returned
        _ts("occurred_at"),
        _ts("observed_at"),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("ix_shipment_events", "shipment_events", ["shipment_id", "occurred_at"])

    op.create_table(
        "external_actions",
        _id(),
        sa.Column("action_kind", sa.String(32), nullable=False),  # sample_shipment
        sa.Column("collaboration_id", sa.String(36), sa.ForeignKey("collaborations.id"), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False, server_default="manual"),
        sa.Column("payload_version", sa.Integer, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        # draft | awaiting_confirmation | confirmed | dispatching | succeeded | failed | unknown
        # | needs_manual_review | cancelled
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("confirmation_id", sa.String(36), nullable=True),
        sa.Column("result", sa.Text, nullable=True),  # JSON，不含个人数据
        _ts("created_at"),
        _ts("updated_at"),
        sa.UniqueConstraint("action_kind", "target_id", "payload_version", name="uq_action_target_version"),
    )

    op.create_table(
        "action_confirmations",
        _id(),
        sa.Column("action_id", sa.String(36), sa.ForeignKey("external_actions.id"), nullable=False),
        sa.Column("payload_version", sa.Integer, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("summary", sa.Text, nullable=False),  # JSON：脱敏摘要
        _ts("confirmed_at"),
        _ts("expires_at"),
        _ts("consumed_at", True),
        _ts("revoked_at", True),
    )


def downgrade() -> None:
    for t in (
        "action_confirmations",
        "external_actions",
        "shipment_events",
        "sample_shipments",
        "collaboration_events",
        "collaborations",
        "creator_relationships",
        "creator_decisions",
    ):
        op.drop_table(t)
