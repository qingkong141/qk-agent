"""add external user scope to conversations

Revision ID: 007
Revises: 006
Create Date: 2026-08-04
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("external_user_id", sa.String(255), server_default="", nullable=False),
    )
    op.create_index(
        "ix_conversations_external_user_id",
        "conversations",
        ["external_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_external_user_id", table_name="conversations")
    op.drop_column("conversations", "external_user_id")
