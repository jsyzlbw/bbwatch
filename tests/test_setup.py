from bbwatch.cli import resolve_setup_credentials


def test_env_vars_take_priority():
    u, p = resolve_setup_credentials(
        {"BBWATCH_USERNAME": "125@link.cuhk.edu.cn", "BBWATCH_PASSWORD": "pw"}
    )
    assert u == "125@link.cuhk.edu.cn" and p == "pw"


def test_stdin_two_lines():
    u, p = resolve_setup_credentials({}, stdin_text="125@link.cuhk.edu.cn\npw\n")
    assert u == "125@link.cuhk.edu.cn" and p == "pw"


def test_none_when_absent():
    assert resolve_setup_credentials({}, None) == (None, None)
    assert resolve_setup_credentials({}, "only-one-line") == (None, None)
