from __future__ import annotations

from dataclasses import dataclass

import keyring
from keyring import errors as keyring_errors

from .errors import CredentialError

SERVICE = "bbwatch"
_USER_KEY = "__username__"


@dataclass
class Credentials:
    username: str
    password: str


def store_credentials(username: str, password: str) -> None:
    try:
        keyring.set_password(SERVICE, _USER_KEY, username)
        keyring.set_password(SERVICE, username, password)
    except keyring_errors.KeyringError as error:
        raise CredentialError("无法访问系统凭据存储，请检查 Windows Credential Manager 或系统钥匙串") from error


def load_credentials() -> Credentials:
    try:
        username = keyring.get_password(SERVICE, _USER_KEY)
        if not username:
            raise CredentialError("未找到凭据，请先运行 bbwatch setup")
        password = keyring.get_password(SERVICE, username)
        if not password:
            raise CredentialError("凭据不完整，请重新运行 bbwatch setup")
    except keyring_errors.KeyringError as error:
        raise CredentialError("无法访问系统凭据存储，请检查 Windows Credential Manager 或系统钥匙串") from error
    return Credentials(username=username, password=password)


def clear_credentials() -> None:
    try:
        username = keyring.get_password(SERVICE, _USER_KEY)
        if username:
            try:
                keyring.delete_password(SERVICE, username)
            except keyring_errors.PasswordDeleteError:
                pass
        try:
            keyring.delete_password(SERVICE, _USER_KEY)
        except keyring_errors.PasswordDeleteError:
            pass
    except keyring_errors.KeyringError as error:
        raise CredentialError("无法访问系统凭据存储，请检查 Windows Credential Manager 或系统钥匙串") from error
