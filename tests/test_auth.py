from pathlib import Path

import pytest

from bbwatch.auth import AUTHORIZE_URL, build_login_post, login, parse_adfs_form
from bbwatch.errors import AuthError, CredentialError
from bbwatch.secrets import Credentials
from bbwatch.transport import FakeTransport, Response

FIX = Path(__file__).parent / "fixtures"


def _login_transport(final_url: str, post_body: str = "") -> FakeTransport:
    html = (FIX / "adfs_form.html").read_text()
    action = "https://sts.cuhk.edu.cn/adfs/oauth2/authorize?client_id=x&response_type=code"
    return FakeTransport(
        {
            ("GET", AUTHORIZE_URL): Response(
                200, {"Content-Type": "text/html"}, html, AUTHORIZE_URL
            ),
            ("POST", action): Response(
                200, {"Content-Type": "text/html"}, post_body, final_url
            ),
        }
    )


def test_login_succeeds_when_landing_on_bb_with_port():
    # 真实环境最终 URL 带 :443，hostname 比较必须忽略端口
    t = _login_transport("https://bb.cuhk.edu.cn:443/webapps/portal/execute/defaultTab")
    login(t, Credentials("u@link.cuhk.edu.cn", "pw"))  # 不抛即成功


def test_login_bad_credentials_raises():
    t = _login_transport(
        "https://sts.cuhk.edu.cn/adfs/oauth2/authorize?x", post_body="Incorrect user ID or password"
    )
    with pytest.raises(CredentialError):
        login(t, Credentials("u@link.cuhk.edu.cn", "bad"))


def test_login_stuck_on_idp_raises_autherror():
    t = _login_transport("https://sts.cuhk.edu.cn/adfs/oauth2/authorize?x", post_body="continue")
    with pytest.raises(AuthError):
        login(t, Credentials("u@link.cuhk.edu.cn", "pw"))


def test_parse_adfs_form_extracts_action_and_fields():
    html = (FIX / "adfs_form.html").read_text()
    action, fields = parse_adfs_form(html, base="https://sts.cuhk.edu.cn/")
    assert action.startswith("https://sts.cuhk.edu.cn/adfs/oauth2/authorize")
    assert "UserName" in fields and "Password" in fields and fields["Kmsi"] == "true"


def test_parse_adfs_form_picks_password_form_not_paginated():
    # 真实页有两个 form：分步(无密码)在前、真正登录表单(含 Password)在后。
    # 必须选含 Password 的那个，否则 POST 缺密码字段 → 登录失败。
    html = (FIX / "adfs_form.html").read_text()
    action, fields = parse_adfs_form(html, base="https://sts.cuhk.edu.cn/")
    assert "Password" in fields  # 选错(分步表单)则没有此字段
    assert "pageid=paginated" not in action  # 不是分步表单的 action


def test_login_clears_stale_cookies_before_authorize():
    # 重登前必须清掉旧 cookie，否则残留的 SSO 会话会让 ADFS 直接跳过登录页(无表单)。
    t = _login_transport("https://bb.cuhk.edu.cn:443/webapps/portal/execute/defaultTab")
    t.import_cookies([{"name": "JSESSIONID", "value": "stale", "domain": "bb", "path": "/"}])
    login(t, Credentials("u@link.cuhk.edu.cn", "pw"))
    assert t.export_cookies() == []  # 旧 cookie 已清


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
