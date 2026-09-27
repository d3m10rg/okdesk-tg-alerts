from __future__ import annotations

import getpass
import logging
import sys

from .clients import AuthenticationError, OkdeskClient, RemoteServiceError
from .config import Config, ConfigError
from .state import CredentialStore, Credentials, StateError, StateStore


def _active_credentials(config: Config, store: CredentialStore) -> Credentials:
    return store.load() or Credentials(config.okdesk_login, config.okdesk_password)


def _show_numbers(store: StateStore) -> None:
    state = store.load()
    numbers = ", ".join(str(value) for value in state.received_ids) or "пока нет"
    cursor = str(state.high_watermark) if state.high_watermark else "не задан"
    print(f"\nПоследние полученные номера (максимум 10): {numbers}")
    print(f"Курсор проверки: {cursor}\n")


def _change_credentials(
    client: OkdeskClient,
    store: CredentialStore,
    *,
    change_login: bool,
    change_password: bool,
) -> None:
    login = client.login
    password = client.password
    if change_login:
        login = input("Новый логин: ").strip()
        if not login:
            print("Логин не изменён: пустое значение запрещено.\n")
            return
    if change_password:
        password = getpass.getpass("Новый пароль (ввод скрыт): ")
        if not password:
            print("Пароль не изменён: пустое значение запрещено.\n")
            return

    try:
        client.replace_credentials(login, password)
    except AuthenticationError:
        print("Okdesk отклонил новые данные. Старые значения сохранены.\n")
        return
    except RemoteServiceError:
        print("Не удалось проверить данные из-за сетевой ошибки. Ничего не изменено.\n")
        return

    store.save(Credentials(login, password))
    print(
        "Учётные данные проверены и сохранены. "
        "Рабочий процесс подхватит их в следующем цикле.\n"
    )


def run() -> int:
    try:
        config = Config.from_env()
        credential_store = CredentialStore(config.credentials_path)
        credentials = _active_credentials(config, credential_store)
        client = OkdeskClient(
            config.okdesk_url,
            credentials.login,
            credentials.password,
            config.request_timeout_seconds,
        )
        state_store = StateStore(config.state_path)
    except (ConfigError, StateError) as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(level=logging.WARNING)
    print("\nЛокальное меню администрирования Okdesk Telegram Alerts")
    while True:
        print("1. Показать последние полученные номера")
        print("2. Изменить логин Okdesk")
        print("3. Изменить пароль Okdesk")
        print("4. Изменить логин и пароль Okdesk")
        print("0. Выход")
        try:
            choice = input("Выберите пункт: ").strip()
            if choice == "1":
                _show_numbers(state_store)
            elif choice == "2":
                _change_credentials(
                    client, credential_store, change_login=True, change_password=False
                )
            elif choice == "3":
                _change_credentials(
                    client, credential_store, change_login=False, change_password=True
                )
            elif choice == "4":
                _change_credentials(
                    client, credential_store, change_login=True, change_password=True
                )
            elif choice == "0":
                return 0
            else:
                print("Неизвестный пункт меню.\n")
        except (EOFError, KeyboardInterrupt):
            print("\nВыход.")
            return 0
        except StateError as exc:
            print(f"Ошибка состояния: {exc}\n", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(run())
