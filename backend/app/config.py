from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT / '.env', ROOT / '.env.local'), extra='ignore')
    app_env: str = 'development'
    app_timezone: str = 'Asia/Hong_Kong'
    app_base_url: str = 'http://localhost:8000'
    allowed_origins: str = 'http://localhost:8000,http://127.0.0.1:8000,http://localhost:5173,http://127.0.0.1:5173'
    database_url: str = f'sqlite:///{(ROOT / "data" / "atlas.db").as_posix()}'
    data_dir: Path = ROOT / 'data'
    backup_dir: Path = ROOT / 'backups'
    admin_password: str = ''
    local_passwordless: bool = False
    cookie_secure: bool = False
    redis_url: str = 'redis://localhost:6379/0'
    task_mode: str = 'local'
    ai_provider: str = 'mock'
    ai_api_key: str = ''
    ai_base_url: str = 'https://api.openai.com/v1'
    ai_text_model: str = ''
    ai_vision_model: str = ''
    ai_asr_model: str = ''
    ai_embedding_model: str = ''
    ai_embedding_base_url: str = ''
    ai_embedding_api_key: str = ''
    ai_embedding_provider: str = 'api'
    ai_embedding_dimension: int = Field(default=96, ge=1, le=2000)
    cloud_calls_enabled: bool = False
    adapter_douyin_mode: str = 'mock'
    adapter_xhs_mode: str = 'mock'
    platform_requests_enabled: bool = False
    brain_sync_enabled: bool = False
    brain_base_url: str = 'http://127.0.0.1:8001'
    reflection_enabled: bool = False
    reflection_provider: str = 'configured'
    reflection_base_url: str = 'http://127.0.0.1:1234/v1'
    reflection_text_model: str = 'creator-atlas-reflection'
    douyin_browser_channel: str = 'msedge'
    douyin_node_path: str = 'node'
    douyin_session_file: Path = ROOT / 'runtime' / 'douyin-session.json'
    douyin_browser_timeout: int = Field(default=60, ge=5, le=120)
    report_hour: int = Field(default=23, ge=0, le=23)
    report_minute: int = Field(default=55, ge=0, le=59)
    min_free_space_mb: int = 256
    max_upload_mb: int = 250
    frame_interval: int = 15
    max_frames: int = 24
    lease_seconds: int = 1800


settings = Settings()
