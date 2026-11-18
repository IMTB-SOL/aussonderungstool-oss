# SPDX-FileCopyrightText: 2026 IMTB Solutions GmbH
#
# SPDX-License-Identifier: EUPL-1.2

"""Spalte original_zip für das Zurücksetzen eines Vorgangs.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-10

Die Spalte wird bewusst NICHT rückwirkend gefüllt: Bei bestehenden Jobs wurde
das ursprüngliche ZIP bereits beim ersten Reparaturschritt gelöscht. Der
aktuelle Stand ist nicht der Ursprungszustand — ihn als Original auszuweisen
würde ein Zurücksetzen vorspiegeln, das keines ist.
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("original_zip", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "original_zip")
