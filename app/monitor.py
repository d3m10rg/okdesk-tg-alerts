from __future__ import annotations

import logging
from typing import Any, Protocol

from .clients import AuthenticationError
from .state import MonitorState, StateStore


LOGGER = logging.getLogger(__name__)


class OkdeskApi(Protocol):
    def authenticate(self) -> None: ...

    def latest_issue_ids(self, limit: int = 10) -> list[int]: ...

    def get_issue(self, issue_id: int) -> dict[str, Any] | None: ...


class TelegramApi(Protocol):
    def send(self, text: str) -> None: ...


def priority_label(issue: dict[str, Any]) -> str:
    priority = issue.get("priority")
    if not isinstance(priority, dict):
        return "обычный"

    code = str(priority.get("code") or "").strip().casefold()
    name = str(priority.get("name") or "").strip().casefold()
    combined = f"{code} {name}"
    if code in {"low", "lowest"} or "низк" in combined:
        return "низкий"
    if code in {"critical", "urgent", "highest"} or any(
        word in combined for word in ("крит", "сроч", "авар")
    ):
        return "критикал"
    if code in {"high"} or "высок" in combined:
        return "высокий"
    if code in {"normal", "medium", "default"} or any(
        word in combined for word in ("обыч", "норм", "средн")
    ):
        return "обычный"
    LOGGER.warning("Unknown Okdesk priority %r; using normal", code or name)
    return "обычный"


def format_alert(issue: dict[str, Any]) -> str:
    issue_id = int(issue["id"])
    title = " ".join(str(issue.get("title") or "Без названия").split())
    if not title:
        title = "Без названия"
    # Telegram's message limit is 4096 characters; leave room for labels.
    title = title[:3900]
    return (
        f"№ заявки: {issue_id}\n"
        f"Название: {title}\n"
        f"Уровень: {priority_label(issue)}"
    )


class IssueMonitor:
    def __init__(
        self,
        okdesk: OkdeskApi,
        telegram: TelegramApi,
        state_store: StateStore,
        max_issues_per_cycle: int = 100,
    ) -> None:
        self.okdesk = okdesk
        self.telegram = telegram
        self.state_store = state_store
        self.max_issues_per_cycle = max_issues_per_cycle

    def _with_reauthentication(self, operation: str, *args: Any) -> Any:
        method = getattr(self.okdesk, operation)
        try:
            return method(*args)
        except AuthenticationError:
            LOGGER.info("Okdesk session expired; authenticating again")
            self.okdesk.authenticate()
            return method(*args)

    def poll_once(self) -> int:
        state = self.state_store.load()
        latest_ids = self._with_reauthentication("latest_issue_ids", 10)

        if not state.initialized:
            state.initialize(latest_ids)
            self.state_store.save(state)
            LOGGER.info(
                "Initial baseline saved: %d number(s), high watermark %d",
                len(state.received_ids),
                state.high_watermark,
            )
            return 0

        if not latest_ids:
            LOGGER.debug("No visible issues returned by Okdesk")
            return 0

        cutoff = max(latest_ids)
        start = state.high_watermark + 1
        if cutoff < start:
            # Never lower the cursor when issues disappear after deletion or merge.
            LOGGER.debug(
                "No new issue numbers; high watermark stays at %d", state.high_watermark
            )
            return 0

        target = min(cutoff, start + self.max_issues_per_cycle - 1)
        sent = 0
        dirty = False
        for issue_id in range(start, target + 1):
            issue = self._with_reauthentication("get_issue", issue_id)
            if issue is None:
                LOGGER.debug("Issue %d is absent, merged, or not visible; skipping", issue_id)
                state.advance(issue_id, received=False)
                dirty = True
                continue

            self.telegram.send(format_alert(issue))
            # Persist only after Telegram accepted the alert. This favors a rare duplicate
            # after a crash over silently losing a notification.
            state.advance(issue_id, received=True)
            self.state_store.save(state)
            dirty = False
            sent += 1
            LOGGER.info("Telegram alert sent for issue %d", issue_id)

        if dirty:
            self.state_store.save(state)
        if target < cutoff:
            LOGGER.warning(
                "Catch-up limited to %d numbers this cycle; %d remains the newest known number",
                self.max_issues_per_cycle,
                cutoff,
            )
        return sent
