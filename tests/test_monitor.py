from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.monitor import IssueMonitor, format_alert, priority_label
from app.state import MAX_STORED_IDS, MonitorState, StateStore


def issue(issue_id: int, priority: str = "normal") -> dict[str, Any]:
    return {
        "id": issue_id,
        "title": f"Заявка {issue_id}",
        "priority": {"code": priority, "name": priority},
    }


class FakeOkdesk:
    def __init__(self, latest: list[int], issues: dict[int, dict[str, Any] | None]):
        self.latest = latest
        self.issues = issues
        self.lookups: list[int] = []

    def authenticate(self) -> None:
        pass

    def latest_issues(self, limit: int = 10) -> list[dict[str, Any]]:
        return [
            dict(self.issues.get(issue_id) or {"id": issue_id})
            for issue_id in self.latest[:limit]
        ]

    def get_issue(self, issue_id: int) -> dict[str, Any] | None:
        self.lookups.append(issue_id)
        return self.issues.get(issue_id)


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str) -> None:
        self.messages.append(text)


class MonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = StateStore(Path(self.temp.name) / "state.json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_first_run_sets_baseline_without_alerts(self) -> None:
        okdesk = FakeOkdesk(list(range(110, 100, -1)), {})
        telegram = FakeTelegram()

        sent = IssueMonitor(okdesk, telegram, self.store).poll_once()

        self.assertEqual(sent, 0)
        self.assertEqual(telegram.messages, [])
        state = self.store.load()
        self.assertTrue(state.initialized)
        self.assertEqual(state.high_watermark, 110)
        self.assertEqual(len(state.received_ids), MAX_STORED_IDS)

    def test_disappearing_issues_do_not_lower_cursor_or_alert(self) -> None:
        state = MonitorState(initialized=True)
        state.initialize(range(101, 91, -1))
        self.store.save(state)
        okdesk = FakeOkdesk(list(range(100, 90, -1)), {})
        telegram = FakeTelegram()

        sent = IssueMonitor(okdesk, telegram, self.store).poll_once()

        self.assertEqual(sent, 0)
        self.assertEqual(telegram.messages, [])
        self.assertEqual(self.store.load().high_watermark, 101)

    def test_new_issues_are_sent_in_order_and_gap_is_skipped(self) -> None:
        state = MonitorState(initialized=True)
        state.initialize(range(10, 0, -1))
        self.store.save(state)
        okdesk = FakeOkdesk(
            [13, 12, 10],
            {11: None, 12: issue(12, "high"), 13: issue(13, "critical")},
        )
        telegram = FakeTelegram()

        sent = IssueMonitor(okdesk, telegram, self.store).poll_once()

        self.assertEqual(sent, 2)
        self.assertEqual(okdesk.lookups, [11])
        self.assertIn("№ заявки: 12", telegram.messages[0])
        self.assertIn("Уровень: высокий", telegram.messages[0])
        self.assertIn("№ заявки: 13", telegram.messages[1])
        self.assertIn("Уровень: критикал", telegram.messages[1])
        stored = self.store.load()
        self.assertEqual(stored.high_watermark, 13)
        self.assertNotIn(11, stored.received_ids)
        self.assertEqual(stored.received_ids[:2], [13, 12])
        self.assertLessEqual(len(stored.received_ids), MAX_STORED_IDS)

    def test_visible_list_item_does_not_depend_on_detail_endpoint(self) -> None:
        state = MonitorState(initialized=True, cursor=10, cursor_received=True)
        self.store.save(state)
        okdesk = FakeOkdesk([11], {11: issue(11, "high")})
        telegram = FakeTelegram()

        sent = IssueMonitor(okdesk, telegram, self.store).poll_once()

        self.assertEqual(sent, 1)
        self.assertEqual(okdesk.lookups, [])
        self.assertIn("№ заявки: 11", telegram.messages[0])
        self.assertEqual(self.store.load().high_watermark, 11)

    def test_cycle_limit_resumes_without_losing_numbers(self) -> None:
        state = MonitorState(initialized=True, cursor=20, cursor_received=True)
        self.store.save(state)
        okdesk = FakeOkdesk([25], {number: issue(number) for number in range(21, 26)})
        telegram = FakeTelegram()
        monitor = IssueMonitor(okdesk, telegram, self.store, max_issues_per_cycle=2)

        self.assertEqual(monitor.poll_once(), 2)
        self.assertEqual(self.store.load().high_watermark, 22)
        self.assertEqual(monitor.poll_once(), 2)
        self.assertEqual(self.store.load().high_watermark, 24)
        self.assertEqual(monitor.poll_once(), 1)
        self.assertEqual(self.store.load().high_watermark, 25)

    def test_state_file_never_contains_more_than_ten_numbers(self) -> None:
        state = MonitorState(initialized=True)
        state.initialize(range(1, 101))
        for issue_id in range(101, 121):
            state.advance(issue_id, received=issue_id % 2 == 0)
        self.store.save(state)

        raw = json.loads(self.store.path.read_text(encoding="utf-8"))
        stored_numbers = [raw["cursor"], *raw["previous_received_ids"]]
        self.assertLessEqual(len(stored_numbers), 10)
        self.assertEqual(len(stored_numbers), len(set(stored_numbers)))
        self.assertLessEqual(len(self.store.load().received_ids), 10)

    def test_alert_format_and_priority_aliases(self) -> None:
        self.assertEqual(priority_label(issue(1, "low")), "низкий")
        self.assertEqual(priority_label(issue(1, "normal")), "обычный")
        self.assertEqual(priority_label(issue(1, "high")), "высокий")
        self.assertEqual(priority_label(issue(1, "urgent")), "критикал")
        self.assertEqual(
            format_alert(issue(42, "normal")),
            "№ заявки: 42\nНазвание: Заявка 42\nУровень: обычный",
        )


if __name__ == "__main__":
    unittest.main()
