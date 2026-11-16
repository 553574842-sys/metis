"""Separate derived learning from immutable source knowledge."""
from alembic import op
from app.models import LearningReflection

revision = '0004'
down_revision = '0003'


def upgrade():
    LearningReflection.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    raise RuntimeError('保留深入学习记录；回退应用版本无需删表。')
