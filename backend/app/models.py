from datetime import datetime
from sqlalchemy import String, Text, JSON, Integer, Float, Boolean, DateTime, ForeignKey, UniqueConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector
from .db import Base, Row, now

DT = DateTime(timezone=True)


class Creator(Row, Base):
    __tablename__ = 'creators'
    platform: Mapped[str] = mapped_column(String(20))
    platform_creator_id: Mapped[str | None] = mapped_column(String(200))
    profile_url: Mapped[str] = mapped_column(Text)
    normalized_profile_url: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    tracking_state: Mapped[str] = mapped_column(String(20), default='tracking')
    history_scan_state: Mapped[str] = mapped_column(String(40), default='pending')
    access_state: Mapped[str] = mapped_column(String(40), default='not_tested')
    source_mode: Mapped[str] = mapped_column(String(20), default='mock')
    last_check_at: Mapped[datetime | None] = mapped_column(DT)
    next_check_at: Mapped[datetime | None] = mapped_column(DT, default=now, index=True)
    __table_args__ = (UniqueConstraint('platform', 'platform_creator_id'),)


class Scan(Row, Base):
    __tablename__ = 'creator_scan_runs'
    creator_id: Mapped[str] = mapped_column(ForeignKey('creators.id'), index=True)
    scan_type: Mapped[str] = mapped_column(String(20))
    cursor: Mapped[str | None] = mapped_column(String(300))
    started_at: Mapped[datetime] = mapped_column(DT, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DT)
    status: Mapped[str] = mapped_column(String(30), default='queued')
    discovered_count: Mapped[int] = mapped_column(Integer, default=0)
    known_count: Mapped[int] = mapped_column(Integer, default=0)
    coverage_state: Mapped[str] = mapped_column(String(80), default='not_proven')
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index('uq_active_creator_scan', 'creator_id', 'scan_type', unique=True,
                           postgresql_where=text("status IN ('queued','running','waiting')"),
                           sqlite_where=text("status IN ('queued','running','waiting')")),)


class Work(Row, Base):
    __tablename__ = 'works'
    creator_id: Mapped[str] = mapped_column(ForeignKey('creators.id'), index=True)
    platform: Mapped[str] = mapped_column(String(20))
    platform_work_id: Mapped[str | None] = mapped_column(String(200))
    identity_unverified: Mapped[bool] = mapped_column(Boolean, default=False)
    original_url: Mapped[str] = mapped_column(Text, default='')
    canonical_url: Mapped[str | None] = mapped_column(Text)
    link_validation: Mapped[str] = mapped_column(String(30), default='not_checked')
    content_type: Mapped[str] = mapped_column(String(20), default='text')
    title: Mapped[str] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text, default='')
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    published_at: Mapped[datetime | None] = mapped_column(DT)
    discovered_at: Mapped[datetime] = mapped_column(DT, default=now)
    processing_state: Mapped[str] = mapped_column(String(30), default='pending_capture', index=True)
    access_state: Mapped[str] = mapped_column(String(40), default='available')
    source_mode: Mapped[str] = mapped_column(String(20))
    current_analysis_id: Mapped[str | None] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, default=0)
    temporary_video: Mapped[str | None] = mapped_column(Text)
    media_context: Mapped[dict] = mapped_column(JSON, default=dict)
    rights_statement: Mapped[str] = mapped_column(Text, default='')
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (Index('uq_work_identity', 'platform', 'platform_work_id', unique=True,
                           postgresql_where=text('platform_work_id IS NOT NULL'), sqlite_where=text('platform_work_id IS NOT NULL')),)


class Asset(Row, Base):
    __tablename__ = 'work_assets'
    work_id: Mapped[str] = mapped_column(ForeignKey('works.id'), index=True)
    asset_type: Mapped[str] = mapped_column(String(20))
    file_path: Mapped[str] = mapped_column(Text)
    source_order: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(Integer)
    storage_class: Mapped[str] = mapped_column(String(20), default='permanent')
    __table_args__ = (UniqueConstraint('work_id', 'asset_type', 'source_order'),)


class Analysis(Row, Base):
    __tablename__ = 'analysis_runs'
    work_id: Mapped[str] = mapped_column(ForeignKey('works.id'), index=True)
    status: Mapped[str] = mapped_column(String(20), default='running')
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(150))
    prompt_version: Mapped[str] = mapped_column(String(30), default='evidence_v1')
    schema_version: Mapped[str] = mapped_column(String(30), default='knowledge_v1')
    started_at: Mapped[datetime] = mapped_column(DT, default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DT)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(60))


class Segment(Row, Base):
    __tablename__ = 'work_segments'
    work_id: Mapped[str] = mapped_column(ForeignKey('works.id'), index=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey('analysis_runs.id'))
    segment_type: Mapped[str] = mapped_column(String(20))
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    raw_text: Mapped[str] = mapped_column(Text)
    zh_text: Mapped[str] = mapped_column(Text)
    image_asset_id: Mapped[str | None] = mapped_column(ForeignKey('work_assets.id'))
    sequence_no: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float, default=1)


class Knowledge(Row, Base):
    __tablename__ = 'knowledge_items'
    work_id: Mapped[str] = mapped_column(ForeignKey('works.id'), index=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey('analysis_runs.id'))
    item_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(Text)
    content_zh: Mapped[str] = mapped_column(Text)
    content_original: Mapped[str] = mapped_column(Text)
    claim_kind: Mapped[str] = mapped_column(String(30))
    confidence: Mapped[float] = mapped_column(Float)
    source_segment_ids: Mapped[list] = mapped_column(JSON)
    tokens: Mapped[str] = mapped_column(Text, default='')


class Embedding(Row, Base):
    __tablename__ = 'knowledge_embeddings'
    knowledge_item_id: Mapped[str] = mapped_column(ForeignKey('knowledge_items.id'), unique=True)
    embedding_model: Mapped[str] = mapped_column(String(150))
    embedding_dimension: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list] = mapped_column(Vector().with_variant(JSON(), 'sqlite'))


class LearningReflection(Row, Base):
    __tablename__ = 'learning_reflections'
    work_id: Mapped[str] = mapped_column(ForeignKey('works.id'), index=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey('analysis_runs.id'), index=True)
    input_digest: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    model: Mapped[str] = mapped_column(String(150))
    provider: Mapped[str] = mapped_column(String(30), default='local')
    sources: Mapped[list] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    embedding_model: Mapped[str] = mapped_column(String(150), default='')
    completed_at: Mapped[datetime | None] = mapped_column(DT)


class Relation(Row, Base):
    __tablename__ = 'knowledge_relations'
    source_item_id: Mapped[str] = mapped_column(ForeignKey('knowledge_items.id'))
    target_item_id: Mapped[str] = mapped_column(ForeignKey('knowledge_items.id'))
    relation_type: Mapped[str] = mapped_column(String(30))
    evidence: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint('source_item_id', 'target_item_id', 'relation_type'),)


class Job(Row, Base):
    __tablename__ = 'processing_jobs'
    idempotency_key: Mapped[str] = mapped_column(String(250), unique=True)
    creator_id: Mapped[str | None] = mapped_column(ForeignKey('creators.id'))
    work_id: Mapped[str | None] = mapped_column(ForeignKey('works.id'))
    job_type: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20), default='queued', index=True)
    stage: Mapped[str] = mapped_column(String(20))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DT, default=now, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DT)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DT, default=now)
    last_error_code: Mapped[str | None] = mapped_column(String(60))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Attempt(Row, Base):
    __tablename__ = 'job_attempts'
    job_id: Mapped[str] = mapped_column(ForeignKey('processing_jobs.id'))
    attempt_no: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DT, default=now)
    ended_at: Mapped[datetime | None] = mapped_column(DT)
    error_category: Mapped[str | None] = mapped_column(String(50))
    error_message_redacted: Mapped[str | None] = mapped_column(Text)


class Alert(Row, Base):
    __tablename__ = 'alerts'
    severity: Mapped[str] = mapped_column(String(20), default='important')
    category: Mapped[str] = mapped_column(String(40))
    creator_id: Mapped[str | None] = mapped_column(ForeignKey('creators.id'))
    work_id: Mapped[str | None] = mapped_column(ForeignKey('works.id'))
    job_id: Mapped[str | None] = mapped_column(ForeignKey('processing_jobs.id'))
    message: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DT)
    resolved_at: Mapped[datetime | None] = mapped_column(DT)


class Report(Row, Base):
    __tablename__ = 'daily_reports'
    report_date: Mapped[str] = mapped_column(String(10))
    timezone: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20))
    generated_at: Mapped[datetime] = mapped_column(DT, default=now)
    content_zh: Mapped[dict] = mapped_column(JSON)
    source_work_ids: Mapped[list] = mapped_column(JSON)
    partial_reason: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint('report_date', 'timezone'),)


class Backup(Row, Base):
    __tablename__ = 'backup_runs'
    target: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DT, default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DT)
    result: Mapped[str] = mapped_column(String(20), default='running')
    manifest_path: Mapped[str | None] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(64))
    restore_verified_at: Mapped[datetime | None] = mapped_column(DT)


class Principal(Row, Base):
    __tablename__ = 'auth_principals'
    name: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)


class Key(Row, Base):
    __tablename__ = 'api_keys'
    name: Mapped[str] = mapped_column(String(100))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(20))
    scope: Mapped[str] = mapped_column(String(20), default='read')
    csrf_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DT)
    revoked_at: Mapped[datetime | None] = mapped_column(DT)


class Audit(Row, Base):
    __tablename__ = 'audit_events'
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60))
    target_type: Mapped[str] = mapped_column(String(40))
    target_id: Mapped[str | None] = mapped_column(String(100))
    timestamp: Mapped[datetime] = mapped_column(DT, default=now)
    details_redacted: Mapped[dict] = mapped_column(JSON, default=dict)


class InboxSubmission(Row, Base):
    __tablename__ = 'inbox_submissions'
    # Credentials are excluded from backups; keep submission provenance without
    # restoring device access. Authorization still requires a valid live Key.
    device_id: Mapped[str] = mapped_column(String(36), index=True)
    work_id: Mapped[str] = mapped_column(ForeignKey('works.id'), index=True)
    __table_args__ = (UniqueConstraint('device_id', 'work_id'),)


class ServiceHeartbeat(Base):
    __tablename__ = 'service_heartbeats'
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DT, default=now)
