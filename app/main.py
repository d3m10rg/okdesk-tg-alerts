from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from .clients import OkdeskClient, TelegramClient
from .config import Config, ConfigError
from .monitor import IssueMonitor
from .state import CredentialStore, Credentials, StateStore


LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor new Okdesk issues")
    parser.add_argument(
        "--once", action="store_true", help="Run one polling cycle and exit"
    )
    return parser


def _active_credentials(config: Config, store: CredentialStore) -> Credentials:
    return store.load() or Credentials(config.okdesk_login, config.okdesk_password)


def run() -> int:
    args = _parser().parse_args()
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    state_store = StateStore(config.state_path)
    credential_store = CredentialStore(config.credentials_path)
    stored_credentials = credential_store.load()
    credentials = stored_credentials or Credentials(
        config.okdesk_login, config.okdesk_password
    )
    if stored_credentials is not None:
        LOGGER.info("Using Okdesk credentials saved through the server admin menu")

    okdesk = OkdeskClient(
        config.okdesk_url,
        credentials.login,
        credentials.password,
        config.request_timeout_seconds,
    )
    telegram = TelegramClient(
        config.telegram_bot_token,
        config.telegram_chat_id,
        config.request_timeout_seconds,
    )
    monitor = IssueMonitor(
        okdesk,
        telegram,
        state_store,
        config.max_issues_per_cycle,
    )

    stop_event = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    backoff = 5
    while not stop_event.is_set():
        started = time.monotonic()
        try:
            desired = _active_credentials(config, credential_store)
            if (desired.login, desired.password) != (okdesk.login, okdesk.password):
                okdesk.replace_credentials(desired.login, desired.password)
                LOGGER.info("Reloaded Okdesk credentials changed through the admin menu")

            sent = monitor.poll_once()
            LOGGER.info("Polling cycle completed; alerts sent: %d", sent)
            backoff = 5
        except Exception:
            # Client exceptions suppress lower-level URLs so secrets stay out of logs.
            LOGGER.exception("Polling cycle failed")
            if args.once:
                return 1
            sleep_for = min(backoff, config.poll_interval_seconds)
            backoff = min(backoff * 2, config.poll_interval_seconds)
            LOGGER.info("Retrying after %d second(s)", sleep_for)
            stop_event.wait(sleep_for)
            continue

        if args.once:
            return 0
        remaining = config.poll_interval_seconds - (time.monotonic() - started)
        if remaining > 0:
            stop_event.wait(remaining)
    LOGGER.info("Monitor stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
