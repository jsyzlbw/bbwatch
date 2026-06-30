from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .errors import AuthError, CredentialError
from .secrets import Credentials
from .transport import Transport

CLIENT_ID = "4b71b947-7b0d-4611-b47e-0ec37aabfd5e"
REDIRECT_URI = (
    "https://bb.cuhk.edu.cn/webapps/bb-SSOIntegrationOAuth2-BBLEARN/authValidate/getCode"
)
AUTHORIZE_URL = (
    "https://sts.cuhk.edu.cn/adfs/oauth2/authorize?response_type=code"
    f"&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
)
BB_HOST = "bb.cuhk.edu.cn"


def parse_adfs_form(html: str, base: str) -> tuple[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    forms = soup.find_all("form")
    if not forms:
        raise AuthError("ADFS 登录页未找到表单（页面结构可能已变）")
    # 新版 ADFS 同页含两个 form：分步表单(只问用户名,无密码)在前、真正登录表单(含 Password)在后。
    # 必须选含 password 输入的那个，否则 POST 缺密码字段 → 登录无法完成。
    form = next(
        (f for f in forms if f.find("input", attrs={"type": "password"})),
        forms[0],
    )
    action = urljoin(base, form.get("action") or "")
    fields = {
        i.get("name"): (i.get("value") or "")
        for i in form.find_all("input")
        if i.get("name")
    }
    return action, fields


def build_login_post(fields: dict, username: str, password: str) -> dict:
    data = dict(fields)
    data["UserName"] = username
    data["Password"] = password
    return data


def login(transport: Transport, creds: Credentials) -> None:
    """走完 ADFS OAuth2，使 transport 的会话持有 BB cookie。失败抛 AuthError。"""
    # 清掉可能残留的旧 cookie：否则半失效的 SSO 会话会让 ADFS 跳过登录页(返回无表单页)。
    clear = getattr(transport, "clear_cookies", None)
    if callable(clear):
        clear()
    r1 = transport.request("GET", AUTHORIZE_URL)
    action, fields = parse_adfs_form(r1.text, base=r1.url or AUTHORIZE_URL)
    data = build_login_post(fields, creds.username, creds.password)
    r2 = transport.request("POST", action, data=data, allow_redirects=True)
    # 注意用 hostname（不含端口）比较：真实响应的 netloc 可能是 bb.cuhk.edu.cn:443
    if urlparse(r2.url).hostname != BB_HOST:
        low = r2.text.lower()
        if any(k in low for k in ("incorrect", "invalid", "try again", "错误")):
            raise CredentialError("ADFS 拒绝：账号或密码错误")
        raise AuthError(f"登录未落到 BB（最终 URL host={urlparse(r2.url).hostname}）")
