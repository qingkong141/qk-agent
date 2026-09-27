"""Private dataset catalogs and multimodal files.

Revision ID: 010
Revises: 009
"""
import sqlalchemy as sa
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def common():
    return [sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_id", sa.String(36), nullable=False),
            sa.Column("external_user_id", sa.String(255), nullable=False, server_default=""),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade():
    op.create_table("dataset_folders", *common(), sa.UniqueConstraint("owner_id", "external_user_id", "name"))
    op.create_table("datasets", *common(),
                    sa.Column("folder_id", sa.String(36), sa.ForeignKey("dataset_folders.id"), nullable=False),
                    sa.Column("description", sa.String(1000), nullable=False, server_default=""),
                    sa.UniqueConstraint("folder_id", "name"))
    op.create_table("dataset_files", *common(),
                    sa.Column("dataset_id", sa.String(36), sa.ForeignKey("datasets.id"), nullable=False),
                    sa.Column("modality", sa.String(20), nullable=False),
                    sa.Column("mime", sa.String(80), nullable=False),
                    sa.Column("size", sa.Integer(), nullable=False),
                    sa.Column("sha256", sa.String(64), nullable=False),
                    sa.Column("row_count", sa.Integer(), nullable=True))
    for table in ("dataset_folders", "datasets", "dataset_files"):
        op.create_index(f"ix_{table}_owner_id", table, ["owner_id"])
    op.create_index("ix_datasets_folder_id", "datasets", ["folder_id"])
    op.create_index("ix_dataset_files_dataset_id", "dataset_files", ["dataset_id"])


def downgrade():
    for table in ("dataset_files", "datasets", "dataset_folders"):
        op.drop_table(table)
