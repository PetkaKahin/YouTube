"""Non-secret configuration. Credentials are loaded separately from mounted files."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", frozen=True)

    app_name: str = "YouTube API"
    cookie_secure: bool = True
    postgres_host: str = "postgres"
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "youtube"
    postgres_user: str = "youtube"
    valkey_host: str = "valkey"
    valkey_port: int = Field(default=6379, ge=1, le=65535)
    rustfs_endpoint: str = "http://rustfs:9000"
    rustfs_bucket: str = "videos"
    job_error_spool_path: str = "/var/lib/youtube/job-errors"
    job_retry_batch_size: int = Field(default=20, ge=1)
