class BbwatchError(Exception):
    ...


class TransportError(BbwatchError):
    ...


class AuthError(BbwatchError):
    ...


class CredentialError(AuthError):
    """凭据无效或缺失。"""


class SessionRefreshError(AuthError):
    """重登 + 重放后仍失败。"""


class AuthCircuitOpenError(AuthError):
    """熔断期内拒绝再尝试，防账号锁定。"""
