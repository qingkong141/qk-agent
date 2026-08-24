"""runtime checkpoints, tool approvals and idempotent clinical execution

Revision ID: 008
Revises: 007
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("clinical_decisions", sa.Column("owner_id", sa.String(36), server_default="", nullable=False))
    op.add_column("clinical_decisions", sa.Column("external_user_id", sa.String(255), server_default="", nullable=False))
    op.add_column("clinical_decisions", sa.Column("idempotency_key", sa.String(128), server_default="", nullable=False))
    op.add_column("clinical_decisions", sa.Column("version", sa.Integer(), server_default="0", nullable=False))
    op.create_index("ix_clinical_decisions_owner_id", "clinical_decisions", ["owner_id"])
    op.create_index("ix_clinical_decisions_external_user_id", "clinical_decisions", ["external_user_id"])
    op.create_unique_constraint(
        "uq_clinical_decision_idempotency", "clinical_decisions",
        ["owner_id", "external_user_id", "idempotency_key"],
    )
    op.create_table(
        "run_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("external_user_id", sa.String(255), server_default="", nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "sequence", name="uq_run_checkpoint_sequence"),
    )
    for column in ("run_id", "conversation_id", "owner_id", "trace_id"):
        op.create_index(f"ix_run_checkpoints_{column}", "run_checkpoints", [column])
    op.create_table(
        "tool_approvals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("external_user_id", sa.String(255), server_default="", nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("arguments_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("requested_arguments", sa.JSON(), nullable=False),
        sa.Column("approved_by", sa.String(36), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "external_user_id", "conversation_id", "tool_name", "arguments_hash", name="uq_tool_approval_request"),
    )
    for column in ("owner_id", "conversation_id", "run_id", "tool_name"):
        op.create_index(f"ix_tool_approvals_{column}", "tool_approvals", [column])
    op.create_table(
        "user_contexts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("external_user_id", sa.String(255), server_default="", nullable=False),
        sa.Column("workspace", sa.String(64), server_default="default", nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "external_user_id", "workspace", name="uq_user_context_scope"),
    )
    op.create_index("ix_user_contexts_owner_id", "user_contexts", ["owner_id"])


def downgrade() -> None:
    op.drop_table("user_contexts")
    op.drop_table("tool_approvals")
    op.drop_table("run_checkpoints")
    op.drop_constraint("uq_clinical_decision_idempotency", "clinical_decisions", type_="unique")
    op.drop_index("ix_clinical_decisions_external_user_id", table_name="clinical_decisions")
    op.drop_index("ix_clinical_decisions_owner_id", table_name="clinical_decisions")
    for column in ("version", "idempotency_key", "external_user_id", "owner_id"):
        op.drop_column("clinical_decisions", column)
