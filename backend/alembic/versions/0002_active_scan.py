"""Prevent simultaneous active scans without discarding scan history."""
from alembic import op
from sqlalchemy import text, inspect

revision='0002'
down_revision='0001'


def upgrade():
    connection=op.get_bind()
    indexes=inspect(connection).get_indexes('creator_scan_runs')
    if any(x['name']=='uq_active_creator_scan' for x in indexes):
        return
    duplicate=connection.execute(text("SELECT creator_id,scan_type,COUNT(*) FROM creator_scan_runs WHERE status IN ('queued','running','waiting') GROUP BY creator_id,scan_type HAVING COUNT(*) > 1")).first()
    if duplicate:
        raise RuntimeError('存在重复的在途扫描，迁移暂停；保留数据，先完成或取消多余扫描后重试。')
    op.create_index('uq_active_creator_scan','creator_scan_runs',['creator_id','scan_type'],unique=True,
                    postgresql_where=text("status IN ('queued','running','waiting')"),
                    sqlite_where=text("status IN ('queued','running','waiting')"))


def downgrade():
    op.drop_index('uq_active_creator_scan',table_name='creator_scan_runs')
