from pathlib import Path

from bbwatch.auth import AUTHORIZE_URL, build_login_post, parse_adfs_form

FIX = Path(__file__).parent / "fixtures"


def test_parse_adfs_form_extracts_action_and_fields():
    html = (FIX / "adfs_form.html").read_text()
    action, fields = parse_adfs_form(html, base="https://sts.cuhk.edu.cn/")
    assert action.startswith("https://sts.cuhk.edu.cn/adfs/oauth2/authorize")
    assert "UserName" in fields and "Password" in fields and fields["Kmsi"] == "true"


def test_build_login_post_fills_credentials():
    fields = {"UserName": "", "Password": "", "Kmsi": "true"}
    data = build_login_post(fields, "u@link.cuhk.edu.cn", "pw")
    assert data["UserName"] == "u@link.cuhk.edu.cn"
    assert data["Password"] == "pw"
    assert data["Kmsi"] == "true"


def test_authorize_url_has_client_and_redirect():
    assert "oauth2/authorize" in AUTHORIZE_URL
    assert "client_id=4b71b947-7b0d-4611-b47e-0ec37aabfd5e" in AUTHORIZE_URL
    assert "getCode" in AUTHORIZE_URL
