from bbwatch.logging_setup import redact


def test_redacts_password_query():
    s = "POST .../authorize?code=abc123&UserName=120000000&Password=secret"
    out = redact(s)
    assert "secret" not in out
    assert "abc123" not in out
    assert "120000000" not in out


def test_redacts_cookie_and_token():
    s = "Cookie: JSESSIONID=ZZZ; s_session_id=YYY  Authorization: Bearer tok"
    out = redact(s)
    assert "ZZZ" not in out and "YYY" not in out and "tok" not in out
