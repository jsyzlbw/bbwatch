import keyring
from keyring.backend import KeyringBackend

from bbwatch import secrets
from bbwatch.errors import CredentialError


class MemKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self._d = {}

    def get_password(self, s, u):
        return self._d.get((s, u))

    def set_password(self, s, u, p):
        self._d[(s, u)] = p

    def delete_password(self, s, u):
        self._d.pop((s, u), None)


def setup_function():
    keyring.set_keyring(MemKeyring())


def test_store_and_load():
    secrets.store_credentials("125090374@link.cuhk.edu.cn", "pw")
    c = secrets.load_credentials()
    assert c.username.endswith("@link.cuhk.edu.cn")
    assert c.password == "pw"


def test_load_missing_raises():
    secrets.clear_credentials()
    try:
        secrets.load_credentials()
        assert False
    except CredentialError:
        pass
