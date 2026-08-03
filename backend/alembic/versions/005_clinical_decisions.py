"""clinical decisions table

Revision ID: 005
Revises: 004
Create Date: 2026-07-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "clinical_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), nullable=True),
        sa.Column("patient_id", sa.String(64), nullable=False),
        sa.Column("patient_name", sa.String(128), nullable=True),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("trigger_source", sa.String(32), nullable=False),
        sa.Column("trigger_text", sa.Text(), nullable=False),
        sa.Column("extracted_info", sa.JSON(), nullable=True),
        sa.Column("action_params", sa.JSON(), nullable=False),
        sa.Column("safety_level", sa.String(16), server_default="low"),
        sa.Column("status", sa.String(20), server_default="pending"),
        sa.Column("confirmed_by", sa.String(36), nullable=True),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_clinical_decisions_patient_id", "clinical_decisions", ["patient_id"])
    op.create_index("ix_clinical_decisions_action_type", "clinical_decisions", ["action_type"])
    op.create_index("ix_clinical_decisions_status", "clinical_decisions", ["status"])
    op.create_index("ix_clinical_decisions_conversation_id", "clinical_decisions", ["conversation_id"])
    op.create_index("ix_clinical_decisions_created_at", "clinical_decisions", ["created_at"])


def downgrade() -> None:
    op.drop_table("clinical_decisions")
