"""add agent runs and message status

Revision ID: 006
Revises: 005
Create Date: 2026-08-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("status", sa.String(20), server_default="completed", nullable=False))
    op.add_column("messages", sa.Column("message_metadata", sa.JSON(), nullable=True))

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("user_message_id", sa.String(36), sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("assistant_message_id", sa.String(36), sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("status", sa.String(20), server_default="running", nullable=False),
        sa.Column("run_metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_runs_conversation_id", table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_column("messages", "message_metadata")
    op.drop_column("messages", "status")
