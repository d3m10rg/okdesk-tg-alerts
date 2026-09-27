from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class ConfigError(ValueError):
    """Raised when the environment configuration is invalid."""


def _secret(name: str) -> str:
    value = os.getenv(name)
    file_name = os.getenv(f"{name}_FILE", "").strip()

    if value is not None and file_name:
        raise ConfigError(f"Set only one of {name} and {name}_FILE")
    if file_name:
        try:
            secret = Path(file_name).read_text(encoding="utf-8").rstrip("\r\n")
        except OSError as exc:
            raise ConfigError(f"Cannot read {name}_FILE") from exc
        if not secret:
            raise ConfigError(f"{name}_FILE is empty")
        return secret
    if value is None or value == "":
        raise ConfigError(f"Required variable {name} is not set")
    return value


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class Config:
    okdesk_url: str
    okdesk_login: str
    okdesk_password: str
    telegram_bot_token: str
    telegram_chat_id: str
    poll_interval_seconds: int
    request_timeout_seconds: int
    max_issues_per_cycle: int
    state_path: Path
    credentials_path: Path
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        url = os.getenv("OKDESK_URL", "https://sd.cpsupport.ru").strip().rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/"):
            raise ConfigError("OKDESK_URL must be an HTTPS origin without a path")

        chat_id = _secret("TELEGRAM_CHAT_ID").strip()
        if not chat_id:
            raise ConfigError("TELEGRAM_CHAT_ID must not be empty")
        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError("LOG_LEVEL has an unsupported value")

        state_path_raw = os.getenv("STATE_PATH", "/data/state.json").strip()
        credentials_path_raw = os.getenv(
            "CREDENTIALS_PATH", "/data/credentials.json"
        ).strip()
        if not state_path_raw or not credentials_path_raw:
            raise ConfigError("Persistent state paths must not be empty")

        return cls(
            okdesk_url=url,
            okdesk_login=_secret("OKDESK_LOGIN"),
            okdesk_password=_secret("OKDESK_PASSWORD"),
            telegram_bot_token=_secret("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=chat_id,
            poll_interval_seconds=_integer("POLL_INTERVAL_SECONDS", 60, 10, 86_400),
            request_timeout_seconds=_integer("REQUEST_TIMEOUT_SECONDS", 30, 5, 120),
            max_issues_per_cycle=_integer("MAX_ISSUES_PER_CYCLE", 100, 1, 10_000),
            state_path=Path(state_path_raw),
            credentials_path=Path(credentials_path_raw),
            log_level=log_level,
        )
