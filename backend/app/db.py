from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import create_engine, event, DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings


def now():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


class Base(DeclarativeBase):
    pass


class Row:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


settings.data_dir.mkdir(parents=True, exist_ok=True)
engine = create_engine(settings.database_url, pool_pre_ping=True,
                       connect_args={'check_same_thread': False, 'timeout': 30} if settings.database_url.startswith('sqlite') else {})
Session = sessionmaker(engine, expire_on_commit=False)
if engine.dialect.name == 'sqlite':
    @event.listens_for(engine, 'connect')
    def configure_sqlite(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA journal_mode=WAL')


def get_db():
    with Session() as db:
        yield db
