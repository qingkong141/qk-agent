"""Visual studio drafts and published application snapshots.

Revision ID: 009
Revises: 008
"""
import sqlalchemy as sa
from alembic import op

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "studio_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("external_user_id", sa.String(255), nullable=False, server_default=""),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("published_revision", sa.Integer(), nullable=True),
        sa.Column("published_config", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_studio_artifacts_owner_id", "studio_artifacts", ["owner_id"])


def downgrade():
    op.drop_index("ix_studio_artifacts_owner_id", table_name="studio_artifacts")
    op.drop_table("studio_artifacts")
