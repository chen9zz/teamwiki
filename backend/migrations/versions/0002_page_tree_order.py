"""Index scoped sibling pagination and child-existence probes.

Revision ID: 0002_page_tree_order
Revises: 0001_initial
"""

from alembic import op

revision = "0002_page_tree_order"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_page_tree_order",
        "pages",
        ["workspace_id", "parent_id", "deleted_at", "title", "id"],
    )


def downgrade():
    op.drop_index("ix_page_tree_order", table_name="pages")
