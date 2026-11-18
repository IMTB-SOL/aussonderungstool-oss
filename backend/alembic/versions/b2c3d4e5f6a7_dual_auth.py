"""dual_auth: provider + nullable Passwoerter

Revision ID: b2c3d4e5f6a7
Revises: f1a2b3c4d5e6
Create Date: 2026-07-06 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("provider", sa.String(), nullable=False, server_default="passwort")
        )
        batch_op.alter_column("password_hash", existing_type=sa.String(), nullable=True)
        batch_op.alter_column("salt", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("salt", existing_type=sa.String(), nullable=False)
        batch_op.alter_column("password_hash", existing_type=sa.String(), nullable=False)
        batch_op.drop_column("provider")
