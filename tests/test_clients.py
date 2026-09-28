from __future__ import annotations

import traceback
import unittest
from typing import Any

import requests

from app.clients import AuthenticationError, OkdeskClient, RemoteServiceError, TelegramClient


class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.last_url = ""
        self.last_params: dict[str, Any] = {}

    def get(
        self,
        url: str,
        params: dict[str, Any],
        timeout: object,
        allow_redirects: bool = True,
    ) -> FakeResponse:
        self.last_url = url
        self.last_params = params
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response

    def post(self, url: str, **_kwargs: Any) -> FakeResponse:
        self.last_url = url
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


class ClientTests(unittest.TestCase):
    def test_rejected_credentials_do_not_replace_working_credentials(self) -> None:
        client = OkdeskClient("https://example.okdesk.ru", "old", "old-password", 30)
        client.api_token = "old-token"
        client.session = FakeSession(  # type: ignore[assignment]
            FakeResponse(422, {"errors": "invalid"})
        )

        with self.assertRaises(AuthenticationError):
            client.replace_credentials("new", "wrong")

        self.assertEqual(client.login, "old")
        self.assertEqual(client.password, "old-password")
        self.assertEqual(client.api_token, "old-token")

    def test_latest_issue_list_is_limited_to_ten(self) -> None:
        client = OkdeskClient("https://example.okdesk.ru", "u", "p", 30)
        client.api_token = "secret"
        session = FakeSession(FakeResponse(200, [{"id": str(i)} for i in range(20, 0, -1)]))
        client.session = session  # type: ignore[assignment]

        result = client.latest_issue_ids(10)

        self.assertEqual(result, list(range(20, 10, -1)))
        self.assertEqual(session.last_params["page[size]"], 10)
        self.assertNotIn("page[number]", session.last_params)
        self.assertEqual(session.last_params["sorting[field]"], "created_at")
        self.assertEqual(session.last_params["sorting[direction]"], "reverse")
        self.assertEqual(session.last_params["api_token"], "secret")

    def test_latest_issues_include_alert_fields_and_normalize_id(self) -> None:
        client = OkdeskClient("https://example.okdesk.ru", "u", "p", 30)
        client.api_token = "secret"
        client.session = FakeSession(  # type: ignore[assignment]
            FakeResponse(
                200,
                [
                    {
                        "id": "42",
                        "title": "Новая заявка",
                        "priority": {"code": "high", "name": "Высокий"},
                    }
                ],
            )
        )

        result = client.latest_issues(10)

        self.assertEqual(result[0]["id"], 42)
        self.assertEqual(result[0]["title"], "Новая заявка")
        self.assertEqual(result[0]["priority"]["code"], "high")

    def test_merged_redirect_payload_is_not_returned_as_new_issue(self) -> None:
        client = OkdeskClient("https://example.okdesk.ru", "u", "p", 30)
        client.api_token = "secret"
        client.session = FakeSession(  # type: ignore[assignment]
            FakeResponse(200, {"id": 99, "title": "target"})
        )

        self.assertIsNone(client.get_issue(42))

    def test_merged_http_redirect_is_skipped(self) -> None:
        client = OkdeskClient("https://example.okdesk.ru", "u", "p", 30)
        client.api_token = "secret"
        client.session = FakeSession(FakeResponse(302, {}))  # type: ignore[assignment]

        self.assertIsNone(client.get_issue(42))

    def test_http_200_without_id_is_skipped_as_absent_issue(self) -> None:
        client = OkdeskClient("https://example.okdesk.ru", "u", "p", 30)
        client.api_token = "secret"
        client.session = FakeSession(  # type: ignore[assignment]
            FakeResponse(200, {"errors": {"issue": ["not found"]}})
        )

        self.assertIsNone(client.get_issue(42))

    def test_request_exception_traceback_does_not_expose_okdesk_token(self) -> None:
        secret = "very-secret-api-token"
        client = OkdeskClient("https://example.okdesk.ru", "u", "p", 30)
        client.api_token = secret
        client.session = FakeSession(
            error=requests.RequestException(
                f"failed https://example.okdesk.ru/issues?api_token={secret}"
            )
        )  # type: ignore[assignment]

        try:
            client.latest_issue_ids()
        except RemoteServiceError as exc:
            rendered = "".join(traceback.format_exception(exc))
        else:
            self.fail("RemoteServiceError was not raised")
        self.assertNotIn(secret, rendered)

    def test_request_exception_traceback_does_not_expose_telegram_token(self) -> None:
        secret = "123456:very-secret-bot-token"
        client = TelegramClient(secret, "@alerts", 30)
        client.session = FakeSession(
            error=requests.RequestException(
                f"failed https://api.telegram.org/bot{secret}/sendMessage"
            )
        )  # type: ignore[assignment]

        try:
            client.send("test")
        except RemoteServiceError as exc:
            rendered = "".join(traceback.format_exception(exc))
        else:
            self.fail("RemoteServiceError was not raised")
        self.assertNotIn(secret, rendered)


if __name__ == "__main__":
    unittest.main()
