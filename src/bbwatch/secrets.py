from __future__ import annotations

from dataclasses import dataclass

import keyring

from .errors import CredentialError

SERVICE = "bbwatch"
_USER_KEY = "__username__"


@dataclass
class Credentials:
    username: str
    password: str


def store_credentials(username: str, password: str) -> None:
    keyring.set_password(SERVICE, _USER_KEY, username)
    keyring.set_password(SERVICE, username, password)


def load_credentials() -> Credentials:
    username = keyring.get_password(SERVICE, _USER_KEY)
    if not username:
        raise CredentialError("未找到凭据，请先运行 bbwatch setup")
    password = keyring.get_password(SERVICE, username)
    if not password:
        raise CredentialError("凭据不完整，请重新运行 bbwatch setup")
    return Credentials(username=username, password=password)


def clear_credentials() -> None:
    username = keyring.get_password(SERVICE, _USER_KEY)
    if username:
        try:
            keyring.delete_password(SERVICE, username)
        except keyring.errors.PasswordDeleteError:
            pass
    try:
        keyring.delete_password(SERVICE, _USER_KEY)
    except keyring.errors.PasswordDeleteError:
        pass
