from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app.admin import _change_credentials, _show_numbers
from app.clients import AuthenticationError
from app.state import CredentialStore, MonitorState, StateStore


class FakeOkdesk:
    def __init__(self) -> None:
        self.login = "old-login"
        self.password = "old-password"
        self.reject = False

    def replace_credentials(self, login: str, password: str) -> None:
        if self.reject:
            raise AuthenticationError("rejected")
        self.login = login
        self.password = password


class AdminMenuTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.state_store = StateStore(root / "state.json")
        self.credential_store = CredentialStore(root / "credentials.json")
        self.client = FakeOkdesk()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_show_numbers_excludes_skipped_gap(self) -> None:
        state = MonitorState()
        state.initialize([10, 9, 8])
        state.advance(11, received=False)
        state.advance(12, received=True)
        self.state_store.save(state)
        output = io.StringIO()

        with redirect_stdout(output):
            _show_numbers(self.state_store)

        self.assertIn("12, 10, 9, 8", output.getvalue())
        self.assertNotIn("12, 11", output.getvalue())

    def test_password_input_is_hidden_validated_and_saved(self) -> None:
        secret = "new-secret"
        with redirect_stdout(io.StringIO()):
            with patch("app.admin.getpass.getpass", return_value=secret):
                _change_credentials(
                    self.client,  # type: ignore[arg-type]
                    self.credential_store,
                    change_login=False,
                    change_password=True,
                )

        saved = self.credential_store.load()
        self.assertIsNotNone(saved)
        self.assertEqual(saved.password, secret)  # type: ignore[union-attr]

    def test_rejected_credentials_are_not_saved(self) -> None:
        self.client.reject = True
        with redirect_stdout(io.StringIO()):
            with patch("app.admin.getpass.getpass", return_value="wrong"):
                _change_credentials(
                    self.client,  # type: ignore[arg-type]
                    self.credential_store,
                    change_login=False,
                    change_password=True,
                )

        self.assertIsNone(self.credential_store.load())


if __name__ == "__main__":
    unittest.main()
