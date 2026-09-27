"""Owned theme domains and dataset-backed logical models."""
import sqlalchemy as sa
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def common():
    return [sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("owner_id", sa.String(36), nullable=False),
            sa.Column("external_user_id", sa.String(255), nullable=False, server_default=""),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())]


def upgrade():
    op.create_table("theme_domains", *common(),
                    sa.Column("description", sa.String(1000), nullable=False, server_default=""),
                    sa.UniqueConstraint("owner_id", "external_user_id", "name"))
    op.create_table("data_models", *common(),
                    sa.Column("domain_id", sa.String(36), sa.ForeignKey("theme_domains.id"), nullable=False),
                    sa.Column("table_name", sa.String(64), nullable=False),
                    sa.Column("layer", sa.String(8), nullable=False),
                    sa.Column("description", sa.String(1000), nullable=False, server_default=""),
                    sa.Column("fields", sa.JSON(), nullable=False),
                    sa.Column("source_file_id", sa.String(36), sa.ForeignKey("dataset_files.id"), nullable=True),
                    sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
                    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
                    sa.UniqueConstraint("domain_id", "table_name"), sa.UniqueConstraint("domain_id", "name"))
    for table in ("theme_domains", "data_models"):
        op.create_index(f"ix_{table}_owner_id", table, ["owner_id"])
    op.create_index("ix_data_models_domain_id", "data_models", ["domain_id"])
    op.create_index("ix_data_models_source_file_id", "data_models", ["source_file_id"])


def downgrade():
    op.drop_table("data_models")
    op.drop_table("theme_domains")
