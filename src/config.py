import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Email / IMAP Settings
    IMAP_SERVER: str = Field(default="imap.gmail.com", description="IMAP server hostname")
    IMAP_PORT: int = Field(default=993, description="IMAP server port (SSL)")
    IMAP_USER: str = Field(default="user@example.com", description="IMAP login email")
    IMAP_PASSWORD: str = Field(default="password", description="IMAP password or App Password")
    IMAP_FOLDER: str = Field(default="INBOX", description="Mailbox folder to monitor")
    IMAP_SCAN_LIMIT: int = Field(
        default=0,
        description="Maximum mailbox messages to scan; 0 scans all existing messages"
    )
    IMAP_GMAIL_ATTACHMENT_QUERY: str = Field(
        default="{filename:xlsx filename:xls filename:csv}",
        description="Gmail X-GM-RAW prefilter for supported report attachments"
    )
    EMAIL_SUBJECT_FILTER: str = Field(
        default="şikayet oranı dağılımı",
        description="Required mail subject text"
    )
    EMAIL_SUBJECT_MATCH_MODE: str = Field(default="exact", description="Subject matching mode: exact or contains")
    EMAIL_SENDER_FILTER: str = Field(default="", description="Sender email filter")
    ENABLE_LOCAL_DOWNLOAD_FALLBACK: bool = Field(
        default=False,
        description="Process files already present in DOWNLOAD_DIR when IMAP is unavailable"
    )
    DOWNLOAD_DIR: Path = Field(default=Path("./downloads"), description="Directory to store downloaded attachments")

    # Metrics Export Settings (.prom output for Grafana)
    PROMETHEUS_METRICS_PATH: Path = Field(
        default=Path("./metrics/email_metrics.prom"),
        description="Target output .prom file path for Prometheus / Grafana textfile collector"
    )
    METRICS_STATE_PATH: Path = Field(
        default=Path("./metrics/cumulative_metrics.json"),
        description="Persistent cumulative metric state (plain JSON, not a database)"
    )
    METRICS_HTTP_HOST: str = Field(default="127.0.0.1", description="Metrics HTTP listen address")
    METRICS_HTTP_PORT: int = Field(default=9464, description="Metrics HTTP listen port")

    # Pipeline Settings
    POLL_INTERVAL_SECONDS: int = Field(default=60, description="Mail polling interval in seconds")
    CHUNK_SIZE: int = Field(default=50000, description="Polars processing chunk size")
    POLARS_MAX_THREADS: int = Field(default=4, description="Polars max thread count")
    STATE_FILE_PATH: Path = Field(default=Path("./downloads/processed_state.json"), description="State file tracking processed emails")

    def ensure_directories(self) -> None:
        """Create necessary directories if they do not exist."""
        self.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.PROMETHEUS_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.METRICS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        if self.STATE_FILE_PATH.parent:
            self.STATE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)


settings = PipelineSettings()
