"""Initial schema; downgrade refuses destructive rollback."""
from alembic import op
from sqlalchemy import text
from app.db import Base
from app import models
from app.config import settings

revision = '0001'
down_revision = None


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name == 'postgresql':
        connection.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
    Base.metadata.create_all(connection)
    if connection.dialect.name == 'postgresql':
        dimension = int(settings.ai_embedding_dimension)
        connection.execute(text(f'CREATE INDEX IF NOT EXISTS ix_embedding_hnsw_{dimension} ON knowledge_embeddings USING hnsw ((embedding::vector({dimension})) vector_cosine_ops) WHERE embedding_dimension = {dimension}'))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_knowledge_tokens_gin ON knowledge_items USING gin (to_tsvector('simple', tokens))"))


def downgrade():
    raise RuntimeError('拒绝删库回滚：请停机并按 docs/DATA_SCHEMA.md 恢复升级前一致性备份。')
