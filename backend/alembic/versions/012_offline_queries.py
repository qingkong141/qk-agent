"""Saved model-backed SQL queries."""
import sqlalchemy as sa
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("offline_queries",
                    sa.Column("id", sa.String(36), primary_key=True),
                    sa.Column("owner_id", sa.String(36), nullable=False),
                    sa.Column("external_user_id", sa.String(255), nullable=False, server_default=""),
                    sa.Column("name", sa.String(200), nullable=False),
                    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
                    sa.Column("domain_id", sa.String(36), sa.ForeignKey("theme_domains.id"), nullable=False),
                    sa.Column("model_ids", sa.JSON(), nullable=False),
                    sa.Column("sql", sa.Text(), nullable=False),
                    sa.Column("row_limit", sa.Integer(), nullable=False, server_default="1000"),
                    sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
                    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
                    sa.UniqueConstraint("domain_id", "name"))
    op.create_index("ix_offline_queries_owner_id", "offline_queries", ["owner_id"])
    op.create_index("ix_offline_queries_domain_id", "offline_queries", ["domain_id"])


def downgrade():
    op.drop_table("offline_queries")
