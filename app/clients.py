from __future__ import annotations

from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class RemoteServiceError(RuntimeError):
    """A remote API returned an unusable response."""


class AuthenticationError(RemoteServiceError):
    """Okdesk authentication failed or expired."""


class OkdeskClient:
    def __init__(self, base_url: str, login: str, password: str, timeout: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.login = login
        self.password = password
        self.timeout = (5, timeout)
        self.api_token: str | None = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "okdesk-telegram-monitor/1.0"})
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    def authenticate(self) -> None:
        self.api_token = self._request_api_key(self.login, self.password)

    def replace_credentials(self, login: str, password: str) -> None:
        if not login or not password:
            raise AuthenticationError("Okdesk login and password must not be empty")
        token = self._request_api_key(login, password)
        self.login = login
        self.password = password
        self.api_token = token

    def _request_api_key(self, login: str, password: str) -> str:
        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/users/sign_in",
                json={"login": login, "password": password},
                timeout=self.timeout,
            )
        except requests.RequestException:
            raise AuthenticationError("Okdesk authentication request failed") from None

        if response.status_code != 200:
            raise AuthenticationError(
                f"Okdesk rejected login or password (HTTP {response.status_code})"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise AuthenticationError("Okdesk returned invalid authentication data") from exc

        token = payload.get("api_key") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise AuthenticationError("Okdesk did not return an API key")
        return token

    def _get(
        self, path: str, params: dict[str, Any], *, allow_redirects: bool = True
    ) -> requests.Response:
        if self.api_token is None:
            self.authenticate()

        safe_params = dict(params)
        safe_params["api_token"] = self.api_token
        try:
            response = self.session.get(
                f"{self.base_url}{path}",
                params=safe_params,
                timeout=self.timeout,
                allow_redirects=allow_redirects,
            )
        except requests.RequestException:
            # Do not include the exception string: requests may put api_token in the URL.
            raise RemoteServiceError("Okdesk request failed") from None

        if response.status_code == 401:
            self.api_token = None
            raise AuthenticationError("Okdesk API key was rejected")
        return response

    def latest_issues(self, limit: int = 10) -> list[dict[str, Any]]:
        response = self._get(
            "/api/v1/issues/list",
            {
                "page[size]": limit,
                "sorting[field]": "created_at",
                "sorting[direction]": "reverse",
            },
        )
        if response.status_code != 200:
            raise RemoteServiceError(
                f"Okdesk issue list returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RemoteServiceError("Okdesk issue list is not valid JSON") from exc
        if not isinstance(payload, list):
            raise RemoteServiceError("Okdesk issue list has an unexpected format")

        issues: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                raise RemoteServiceError("Okdesk issue list contains an invalid item")
            issue_id = item.get("id")
            if isinstance(issue_id, bool):
                raise RemoteServiceError("Okdesk returned an invalid issue number")
            try:
                issue_id = int(issue_id)
            except (TypeError, ValueError) as exc:
                raise RemoteServiceError("Okdesk returned an invalid issue number") from exc
            if issue_id > 0:
                normalized = dict(item)
                normalized["id"] = issue_id
                issues.append(normalized)
        return issues[:limit]

    def latest_issue_ids(self, limit: int = 10) -> list[int]:
        return [int(issue["id"]) for issue in self.latest_issues(limit)]

    def get_issue(self, issue_id: int) -> dict[str, Any] | None:
        response = self._get(
            f"/api/v1/issues/{issue_id}", {}, allow_redirects=False
        )
        if response.status_code in {301, 302, 303, 307, 308, 403, 404, 422}:
            # Deleted, merged, or not visible to this account.
            return None
        if response.status_code != 200:
            raise RemoteServiceError(
                f"Okdesk issue lookup returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RemoteServiceError("Okdesk issue response is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise RemoteServiceError("Okdesk issue response has an unexpected format")

        returned_id = payload.get("id")
        if returned_id is None:
            # Okdesk can answer HTTP 200 with an error-shaped JSON object for an
            # issue that was deleted, merged, or is no longer visible. It has no
            # top-level id and must be treated as an absent sequential number.
            return None
        try:
            returned_id = int(returned_id)
        except (TypeError, ValueError) as exc:
            raise RemoteServiceError("Okdesk issue response has no valid number") from exc
        if returned_id != issue_id:
            # A merged issue must not create another alert for its destination issue.
            return None
        return payload


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, timeout: int) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = (5, timeout)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "okdesk-telegram-monitor/1.0"})

    def _call(self, method: str, payload_data: dict[str, Any]) -> Any:
        try:
            response = self.session.post(
                f"https://api.telegram.org/bot{self.bot_token}/{method}",
                json=payload_data,
                timeout=self.timeout,
            )
        except requests.RequestException:
            # Do not include the exception string: it may contain the bot token.
            raise RemoteServiceError("Telegram request failed") from None

        try:
            payload = response.json()
        except ValueError as exc:
            raise RemoteServiceError(
                f"Telegram returned invalid data (HTTP {response.status_code})"
            ) from exc
        if response.status_code != 200 or not isinstance(payload, dict) or not payload.get("ok"):
            description = payload.get("description") if isinstance(payload, dict) else None
            suffix = f": {description}" if isinstance(description, str) else ""
            raise RemoteServiceError(
                f"Telegram rejected {method} (HTTP {response.status_code}){suffix}"
            )
        return payload.get("result")

    def send(self, text: str) -> dict[str, Any]:
        data: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        result = self._call("sendMessage", data)
        if not isinstance(result, dict):
            raise RemoteServiceError("Telegram sendMessage result has an unexpected format")
        return result
