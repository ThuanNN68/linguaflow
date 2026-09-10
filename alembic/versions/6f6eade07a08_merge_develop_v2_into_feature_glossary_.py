"""merge develop_v2 into feature/glossary-audience-honorific

Revision ID: 6f6eade07a08
Revises: 48e3856b4fc6, f4b1a2c3d5e6
Create Date: 2026-08-24 17:15:49.490894

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6f6eade07a08'
down_revision: Union[str, Sequence[str], None] = ('48e3856b4fc6', 'f4b1a2c3d5e6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
