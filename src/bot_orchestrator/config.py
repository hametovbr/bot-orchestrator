from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Orchestrator configuration, read from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Password for the web UI.
    admin_password: str
    # Any long random string. Encrypts bot tokens and secrets, signs sessions.
    secret_key: str
    # Shared secret for orchestrator <-> runner calls.
    runner_secret: str

    # Local Telegram Bot API server.
    bot_api_url: str = "http://telegram-bot-api:8081"
    # Cloud Bot API, used only to move bots to the local server (logOut).
    cloud_api_url: str = "https://api.telegram.org"
    # bot-runner base URL.
    runner_url: str = "http://bot-runner:8000"

    database_path: str = "/data/orchestrator.db"
    # Root folder for bot files; each bot gets <files_root>/<bot id>.
    files_root: str = "/files"

    # How long to keep the update log.
    update_retention_days: int = 14
    # Handlers running at the same time for one bot.
    bot_concurrency: int = 4
    # HTTP timeout for a call to the runner; keep it above the runner's HANDLER_TIMEOUT.
    runner_request_timeout: float = 180.0
    # Long-polling timeout for getUpdates, in seconds.
    polling_timeout: int = 25
    # Mark the session cookie Secure (enable when the UI is served over HTTPS).
    secure_cookies: bool = False

    # Data directory of the local Bot API server (same path in every container).
    bot_api_data_dir: str = "/var/lib/telegram-bot-api"
    # Delete files the Bot API server downloaded after this many hours (0 keeps them).
    cache_retention_hours: int = 24
    # Background tasks running at the same time (across all bots).
    task_concurrency: int = 2
    # HTTP timeout for a background task call; keep it above the runner's TASK_TIMEOUT.
    task_request_timeout: float = 7200.0
    # Public address of the UI, used for links in notifications, e.g. https://bots.example.com
    public_url: str = ""
    # Timezone for schedules (IANA name). Read from the standard TZ variable.
    tz: str = "UTC"
    # Send a notification when the disk with bot files is fuller than this, in percent.
    disk_warning_percent: int = 90


@lru_cache
def get_settings() -> Settings:
    return Settings()
