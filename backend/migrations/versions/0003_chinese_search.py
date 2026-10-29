"""Bounded, weighted jieba search fields and existing-page backfill.

Revision ID: 0003_chinese_search
Revises: 0002_page_tree_order

Algorithm app.search_tokenizer_v1 is frozen with jieba==0.42.1. Later algorithms
must get their own module and migration; importing mutable services here would
make historical backfills irreproducible. English's original vector is retained.
"""

import sqlalchemy as sa
from alembic import op

from app.search_tokenizer_v1 import document_terms

revision = "0003_chinese_search"
down_revision = "0002_page_tree_order"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pages", sa.Column("search_title_tokens", sa.Text(), nullable=False, server_default=""))
    op.add_column("pages", sa.Column("search_content_tokens", sa.Text(), nullable=False, server_default=""))
    op.add_column(
        "pages", sa.Column("search_index_truncated", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    connection = op.get_bind()
    # Stable keyset batches avoid loading the whole wiki or keeping a server-side
    # cursor open while UPDATE statements execute. Fetch one character beyond the
    # source prefix to preserve the truncation flag without loading whole bodies.
    # This migration is transactional;
    # writers must be stopped for schema upgrade, as with the initial DDL.
    last_id = ""
    while True:
        batch = (
            connection.execute(
                sa.text(
                    "SELECT id,title,left(content,32769) AS content "
                    "FROM pages WHERE id > :last ORDER BY id LIMIT 100"
                ),
                {"last": last_id},
            )
            .mappings()
            .all()
        )
        if not batch:
            break
        for row in batch:
            title, body, truncated = document_terms(row["title"], row["content"])
            connection.execute(
                sa.text("""
                    UPDATE pages SET search_title_tokens=:title,
                        search_content_tokens=:body, search_index_truncated=:truncated
                    WHERE id=:id
                """),
                {"id": row["id"], "title": title, "body": body, "truncated": truncated},
            )
        last_id = batch[-1]["id"]
    op.execute("""
        ALTER TABLE pages ADD COLUMN search_tokens_vector tsvector GENERATED ALWAYS AS (
            setweight(to_tsvector('simple'::regconfig, search_title_tokens), 'A') ||
            setweight(to_tsvector('simple'::regconfig, search_content_tokens), 'B')
        ) STORED
    """)
    op.create_index(
        "ix_pages_search_tokens_vector", "pages", ["search_tokens_vector"], postgresql_using="gin"
    )


def downgrade():
    op.drop_index("ix_pages_search_tokens_vector", table_name="pages")
    op.drop_column("pages", "search_tokens_vector")
    op.drop_column("pages", "search_index_truncated")
    op.drop_column("pages", "search_content_tokens")
    op.drop_column("pages", "search_title_tokens")
