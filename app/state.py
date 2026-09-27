from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


MAX_STORED_IDS = 10


class StateError(RuntimeError):
    """Persistent state cannot be trusted or saved."""


def _positive_id(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StateError(f"{field_name} contains an invalid number")
    return value


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, path)
    except OSError as exc:
        raise StateError(f"Cannot write state file {path}") from exc


@dataclass
class MonitorState:
    initialized: bool = False
    cursor: int = 0
    cursor_received: bool = False
    previous_received_ids: list[int] = field(default_factory=list)

    @property
    def high_watermark(self) -> int:
        return self.cursor

    @property
    def received_ids(self) -> list[int]:
        current = [self.cursor] if self.cursor > 0 and self.cursor_received else []
        return (current + self.previous_received_ids)[:MAX_STORED_IDS]

    def initialize(self, issue_ids: Iterable[int]) -> None:
        clean = sorted(
            {
                int(issue_id)
                for issue_id in issue_ids
                if not isinstance(issue_id, bool) and int(issue_id) > 0
            },
            reverse=True,
        )[:MAX_STORED_IDS]
        self.initialized = True
        if not clean:
            self.cursor = 0
            self.cursor_received = False
            self.previous_received_ids = []
            return
        self.cursor = clean[0]
        self.cursor_received = True
        self.previous_received_ids = clean[1:MAX_STORED_IDS]

    def advance(self, issue_id: int, *, received: bool) -> None:
        issue_id = int(issue_id)
        if issue_id <= self.cursor:
            return
        previous = list(self.previous_received_ids)
        if self.cursor > 0 and self.cursor_received:
            previous.insert(0, self.cursor)
        self.cursor = issue_id
        self.cursor_received = received
        # The cursor is one stored ticket number. Keep at most nine older numbers,
        # so the complete persisted state never contains more than ten ticket IDs.
        self.previous_received_ids = list(dict.fromkeys(previous))[: MAX_STORED_IDS - 1]


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> MonitorState:
        if not self.path.exists():
            return MonitorState()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"Cannot read state file {self.path}") from exc

        if not isinstance(payload, dict):
            raise StateError(f"Invalid state data in {self.path}")
        if payload.get("version") == 1:
            return self._migrate_v1(payload)
        if payload.get("version") != 2:
            raise StateError(f"Unsupported state format in {self.path}")

        initialized = payload.get("initialized")
        cursor = payload.get("cursor")
        cursor_received = payload.get("cursor_received")
        raw_previous = payload.get("previous_received_ids")
        if (
            not isinstance(initialized, bool)
            or isinstance(cursor, bool)
            or not isinstance(cursor, int)
            or cursor < 0
            or not isinstance(cursor_received, bool)
            or not isinstance(raw_previous, list)
        ):
            raise StateError(f"Invalid state data in {self.path}")
        if len(raw_previous) > MAX_STORED_IDS - 1:
            raise StateError(f"State file contains more than {MAX_STORED_IDS} numbers")

        previous = [_positive_id(value, "previous_received_ids") for value in raw_previous]
        if len(previous) != len(set(previous)) or cursor in previous:
            raise StateError("State file contains duplicate issue numbers")
        if any(value >= cursor for value in previous) and cursor > 0:
            raise StateError("State file contains issue numbers newer than its cursor")
        if cursor == 0 and (cursor_received or previous):
            raise StateError("Empty state has inconsistent issue numbers")
        return MonitorState(initialized, cursor, cursor_received, previous)

    def _migrate_v1(self, payload: dict[str, Any]) -> MonitorState:
        initialized = payload.get("initialized")
        raw_ids = payload.get("recent_ids")
        if not isinstance(initialized, bool) or not isinstance(raw_ids, list):
            raise StateError(f"Invalid legacy state data in {self.path}")
        if len(raw_ids) > MAX_STORED_IDS:
            raise StateError(f"State file contains more than {MAX_STORED_IDS} numbers")
        ids = sorted(
            {_positive_id(value, "recent_ids") for value in raw_ids}, reverse=True
        )
        state = MonitorState(initialized=initialized)
        if ids:
            state.cursor = ids[0]
            state.cursor_received = True
            state.previous_received_ids = ids[1:MAX_STORED_IDS]
        return state

    def save(self, state: MonitorState) -> None:
        if len(state.previous_received_ids) > MAX_STORED_IDS - 1:
            raise StateError(f"Refusing to store more than {MAX_STORED_IDS} numbers")
        payload = {
            "version": 2,
            "initialized": state.initialized,
            "cursor": state.cursor,
            "cursor_received": state.cursor_received,
            "previous_received_ids": state.previous_received_ids,
        }
        _write_private_json(self.path, payload)


@dataclass(frozen=True)
class Credentials:
    login: str
    password: str


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Credentials | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"Cannot read credentials file {self.path}") from exc
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise StateError(f"Unsupported credentials format in {self.path}")
        login = payload.get("login")
        password = payload.get("password")
        if (
            not isinstance(login, str)
            or not login
            or not isinstance(password, str)
            or not password
        ):
            raise StateError(f"Invalid credentials data in {self.path}")
        return Credentials(login, password)

    def save(self, credentials: Credentials) -> None:
        if not credentials.login or not credentials.password:
            raise StateError("Login and password must not be empty")
        _write_private_json(
            self.path,
            {
                "version": 1,
                "login": credentials.login,
                "password": credentials.password,
            },
        )
