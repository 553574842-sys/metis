import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from sqlalchemy import select, create_engine, DateTime, text, func
from sqlalchemy.engine import make_url
from .config import settings, ROOT
from .db import Base, Session, now
from .models import Asset, Backup, Job, Work
from .media import under
from .adapters import ProcessingError

EXCLUDED = {'api_keys', 'auth_principals', 'service_heartbeats'}


def json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, 'tolist'):
        return value.tolist()
    raise TypeError(type(value).__name__)


def checksum(path):
    value = hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(1024*1024), b''):
            value.update(chunk)
    return value.hexdigest()


def create_backup():
    target = settings.backup_dir.resolve()
    data_root = settings.data_dir.resolve()
    if target.is_relative_to(data_root) or data_root.is_relative_to(target) or settings.backup_dir.is_symlink():
        raise ProcessingError('BACKUP_PATH_INVALID', '备份目录必须独立于素材目录，且不能使用符号链接。', False, severe=True)
    target.mkdir(parents=True, exist_ok=True)
    identity = now().strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    directory = target / (identity+'.partial')
    directory.mkdir()
    with Session() as db:
        row = Backup(target=str(target))
        db.add(row)
        db.commit()
        backup_id = row.id
    try:
        # PostgreSQL REPEATABLE READ / SQLite 显式读事务，获取单个一致性数据库快照。
        with Session() as db:
            if db.bind.dialect.name == 'postgresql':
                db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            else:
                db.execute(text('BEGIN'))
            snapshot = {}
            for table in Base.metadata.sorted_tables:
                if table.name in EXCLUDED:
                    continue
                snapshot[table.name] = [dict(record) for record in db.execute(select(table)).mappings()]
            assets = list(db.scalars(select(Asset)))
            database_file = directory / 'database.json'
            database_file.write_text(json.dumps(snapshot, ensure_ascii=False, default=json_default), encoding='utf-8')
            files = {'database.json': checksum(database_file)}
            required_space = sum(asset.byte_size for asset in assets) + database_file.stat().st_size
            if shutil.disk_usage(target).free < required_space + 128*1024*1024:
                raise ProcessingError('BACKUP_SPACE_LOW', '备份目标空间不足。', False, severe=True)
            for asset in assets:
                source = under(settings.data_dir, settings.data_dir / asset.file_path)
                if not source.is_file() or checksum(source) != asset.sha256:
                    raise ProcessingError('BACKUP_ASSET_INVALID', '素材缺失或校验和不一致，备份未完成。', False, severe=True)
                output = under(directory, directory / 'media' / asset.file_path)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, output)
                files[output.relative_to(directory).as_posix()] = checksum(output)
            example = ROOT / '.env.example'
            learning_context = settings.data_dir / 'learning-context.json'
            if learning_context.is_file():
                shutil.copy2(learning_context, directory / 'learning-context.json')
                files['learning-context.json'] = checksum(directory / 'learning-context.json')
            if example.exists():
                shutil.copy2(example, directory / 'config-template.env')
                files['config-template.env'] = checksum(directory / 'config-template.env')
        manifest = {'schema_version': '0004', 'created_at': now().isoformat(), 'backup_id': backup_id,
                    'database_format': 'sqlalchemy-consistent-json-v1', 'files': files,
                    'excluded': sorted(EXCLUDED), 'temporary_media_excluded': True,
                    'pending_media_note': '未完成视频的临时原始文件不纳入长期备份；恢复后需重新提供缺失素材。'}
        manifest_file = directory / 'manifest.json'
        manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        manifest_hash = checksum(manifest_file)
        completed = target / identity
        directory.rename(completed)
        with Session() as db:
            row = db.get(Backup, backup_id)
            row.result, row.completed_at = 'success', now()
            row.manifest_path, row.checksum = str(completed / 'manifest.json'), manifest_hash
            db.commit()
        return {'backup_id': backup_id, 'directory': str(completed), 'checksum': manifest_hash}
    except Exception:
        with Session() as db:
            row = db.get(Backup, backup_id)
            row.result, row.completed_at = 'failed', now()
            from .jobs import alert_once
            alert_once(db, 'backup_failure', '数据备份失败；请检查目标、空间和素材完整性。')
            db.commit()
        raise


def restore_backup(directory: Path, destination_data: Path, destination_url: str):
    directory = directory.resolve()
    manifest_path = directory / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest.get('schema_version') not in {'0002', '0003', '0004'}:
        raise ValueError('备份版本与当前迁移版本不一致，请使用对应版本源码恢复')
    if destination_data.resolve().is_relative_to(directory) or directory.is_relative_to(destination_data.resolve()):
        raise ValueError('恢复目标不能包含备份目录，也不能在备份目录中')
    if destination_data.exists() and any(destination_data.iterdir()):
        raise ValueError('恢复目标必须是新的空目录；不会覆盖现有素材')
    for relative, expected in manifest['files'].items():
        path = under(directory, directory / relative)
        if not path.is_file() or checksum(path) != expected:
            raise ValueError('备份文件校验失败：'+relative)
    destination_data.mkdir(parents=True, exist_ok=True)
    target_engine = create_engine(destination_url)
    if target_engine.url == make_url(settings.database_url):
        raise ValueError('禁止向当前运行数据库恢复，请使用隔离的新数据库')
    from sqlalchemy import inspect
    existing_tables = inspect(target_engine).get_table_names()
    if existing_tables:
        raise ValueError('恢复数据库必须为空；不会清空现有数据库')
    with target_engine.begin() as connection:
        if connection.dialect.name == 'postgresql':
            connection.execute(text('CREATE EXTENSION IF NOT EXISTS vector'))
        Base.metadata.create_all(connection)
        snapshot = json.loads((directory / 'database.json').read_text(encoding='utf-8'))
        for table in Base.metadata.sorted_tables:
            rows = snapshot.get(table.name, [])
            for row in rows:
                for column in table.columns:
                    if isinstance(column.type, DateTime) and row.get(column.name):
                        row[column.name] = datetime.fromisoformat(row[column.name])
                if table.name == 'works':
                    row['temporary_video'] = None
                if table.name == 'processing_jobs' and row['state'] == 'running':
                    row['state'], row['lease_token'], row['lease_expires_at'] = 'queued', None, None
            if rows:
                connection.execute(table.insert(), rows)
        connection.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)'))
        connection.execute(text("INSERT INTO alembic_version VALUES ('0004')"))
        dimension = settings.ai_embedding_dimension
        if connection.dialect.name == 'postgresql':
            connection.execute(text(f'CREATE INDEX ix_embedding_hnsw_{dimension} ON knowledge_embeddings USING hnsw ((embedding::vector({dimension})) vector_cosine_ops) WHERE embedding_dimension = {dimension}'))
            connection.execute(text("CREATE INDEX ix_knowledge_tokens_gin ON knowledge_items USING gin (to_tsvector('simple', tokens))"))
        for relative in manifest['files']:
            if relative == 'learning-context.json':
                from .reflections import LearningContext
                LearningContext.model_validate_json((directory / relative).read_text(encoding='utf-8'))
                shutil.copy2(directory / relative, destination_data / relative)
            if relative.startswith('media/'):
                target = under(destination_data, destination_data / relative[6:])
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(directory / relative, target)
                if checksum(target) != manifest['files'][relative]:
                    raise ValueError('恢复后素材校验失败')
        connection.execute(Backup.__table__.update().where(Backup.id == manifest['backup_id']).values(result='success', restore_verified_at=now(), completed_at=now()))
    with target_engine.connect() as connection:
        counts = {table.name: connection.execute(select(func.count()).select_from(table)).scalar() for table in Base.metadata.sorted_tables if table.name not in EXCLUDED}
    target_engine.dispose()
    with Session() as db:
        source = db.get(Backup, manifest['backup_id'])
        if source:
            source.restore_verified_at = now()
            db.commit()
    return {'status':'restored_verified','counts':counts,'credentials_restored':False,'temporary_video_restored':False}
