"""Hornet: groups, group members, group messages, map markers

Revision ID: a1b2c3d4h001
Revises: 434be4e1896a
Create Date: 2026-10-30

Hornet fork only. If an upstream merge adds a migration on top of 434be4e1896a,
re-point that migration's down_revision (or ours) so there is a single head.
"""

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4h001"
down_revision = "434be4e1896a"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "hornet_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "hornet_group_members",
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("hornet_groups.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("added_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "hornet_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("hornet_groups.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_hornet_messages_group_id_id", "hornet_messages", ["group_id", "id"])
    op.create_table(
        "hornet_markers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="point"),
        sa.Column("color", sa.String(16), nullable=False, server_default="#e0b341"),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("hornet_markers")
    op.drop_index("ix_hornet_messages_group_id_id", table_name="hornet_messages")
    op.drop_table("hornet_messages")
    op.drop_table("hornet_group_members")
    op.drop_table("hornet_groups")
