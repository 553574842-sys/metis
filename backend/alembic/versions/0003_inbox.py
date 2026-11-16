"""Add mobile submission history without changing existing works or knowledge."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'


def upgrade():
    if sa.inspect(op.get_bind()).has_table('inbox_submissions'):
        return
    op.create_table('inbox_submissions',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('device_id', sa.String(36), nullable=False),
        sa.Column('work_id', sa.String(36), sa.ForeignKey('works.id'), nullable=False),
        sa.UniqueConstraint('device_id', 'work_id'))
    op.create_index('ix_inbox_submissions_device_id', 'inbox_submissions', ['device_id'])
    op.create_index('ix_inbox_submissions_work_id', 'inbox_submissions', ['work_id'])


def downgrade():
    op.drop_table('inbox_submissions')
