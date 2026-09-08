from types import SimpleNamespace

import pytest
from curl_cffi import CurlOpt
from curl_cffi.requests import exceptions as curl_errors

from bbwatch import transport as transport_module
from bbwatch.errors import TransportError
from bbwatch.transport import CurlCffiTransport, FakeTransport, Response


@pytest.fixture(autouse=True)
def offline_proxy_resolver(monkeypatch):
    # Transport tests never inspect local proxy availability or the user's network.
    monkeypatch.setattr(transport_module, "resolve_proxy", lambda url: "", raising=False)


def test_fake_transport_serves_scripted_responses():
    t = FakeTransport(
        routes={
            ("GET", "https://x/api"): Response(
                200, {"Content-Type": "application/json"}, '{"a":1}', "https://x/api"
            ),
        }
    )
    r = t.request("GET", "https://x/api")
    assert r.status == 200
    assert r.json() == {"a": 1}


def test_response_json_none_on_nonjson():
    r = Response(200, {"Content-Type": "text/html"}, "<html>", "u")
    assert r.json() is None


SENSITIVE_URL = (
    "https://alice:private-password@sts.cuhk.edu.cn:443/private-path"
    "?code=authorization-secret&token=token-secret#fragment-secret"
)


class FailingSession:
    def __init__(self, error):
        self.error = error
        self.calls = []
        self.curl_options = {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        raise self.error

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


def failing_transport(error):
    transport = CurlCffiTransport.__new__(CurlCffiTransport)
    transport._sess = FailingSession(error)
    transport._timeout = 30.0
    return transport


@pytest.mark.parametrize(
    ("exception_class", "code", "expected"),
    [
        (curl_errors.ConnectionError, 7, "无法建立网络连接"),
        (curl_errors.ConnectionError, 52, "连接提前关闭"),
        (curl_errors.ConnectionError, 55, "网络连接中断"),
        (curl_errors.ConnectionError, 56, "网络连接中断"),
        (curl_errors.DNSError, 6, "无法解析服务器地址"),
        (curl_errors.ProxyError, 5, "无法解析代理地址"),
        (curl_errors.Timeout, 28, "连接超时"),
        (curl_errors.SSLError, 35, "安全连接建立失败"),
        (curl_errors.CertificateVerifyError, 60, "证书验证失败"),
    ],
)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_transport_errors_preserve_safe_diagnostics_without_retry(exception_class, code,
                                                                 expected, method):
    original = exception_class(
        "low-level secret: password=private-password code=authorization-secret "
        "http://proxy-user:proxy-password@proxy.test:7890/private-path?token=token-secret",
        code=code,
    )
    transport = failing_transport(original)
    data = {"Password": "credential-secret"} if method == "POST" else None

    with pytest.raises(TransportError) as caught:
        transport.request(method, SENSITIVE_URL, data=data)

    message = str(caught.value)
    assert expected in message
    assert "sts.cuhk.edu.cn:443" in message
    assert exception_class.__name__ in message
    assert f"curl {code}" in message
    for secret in (
        "alice", "private-password", "private-path", "authorization-secret", "token-secret",
        "fragment-secret", "proxy-user", "proxy-password", "credential-secret", "https://",
    ):
        assert secret not in message
    assert caught.value.__cause__ is original
    assert transport._sess.calls == [
        (method, SENSITIVE_URL, {"data": data, "headers": None,
                                "allow_redirects": True, "timeout": 30.0, "proxy": None})
    ]


@pytest.mark.parametrize(
    ("proxy_segment", "expected_endpoint"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("127.0.0.1:7890", "127.0.0.1:7890"),
        ("http://proxy-user:proxy-password@proxy.test:7890/private-path?token=token-secret",
         "proxy.test:7890"),
        ("[::1]:7890", "[::1]:7890"),
    ],
)
def test_confirmed_proxy_connection_failure_is_identified_safely(proxy_segment,
                                                                expected_endpoint):
    original = curl_errors.ConnectionError(
        f"Failed to connect to sts.cuhk.edu.cn:443 over proxy {proxy_segment} "
        "after 0 ms: Could not connect to server", code=7,
    )
    transport = failing_transport(original)
    with pytest.raises(TransportError) as caught:
        transport.request("GET", SENSITIVE_URL)

    message = str(caught.value)
    assert f"无法连接代理（{expected_endpoint}）" in message
    assert "启动或检查代理服务" in message
    assert "sts.cuhk.edu.cn:443" in message
    assert "curl 7" in message
    assert all(secret not in message for secret in (
        "proxy-user", "proxy-password", "private-path", "token-secret", "private-password",
    ))
    assert caught.value.__cause__ is original
    assert len(transport._sess.calls) == 1


def test_proxy_environment_alone_does_not_blame_proxy(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://proxy-user:proxy-password@proxy.test:7890")
    transport = failing_transport(curl_errors.ConnectionError(
        "Failed to connect to sts.cuhk.edu.cn port 443: Could not connect to server", code=7,
    ))
    with pytest.raises(TransportError) as caught:
        transport.request("GET", SENSITIVE_URL)
    assert "无法建立网络连接" in str(caught.value)
    assert "无法连接代理" not in str(caught.value)
    assert "proxy.test" not in str(caught.value)


@pytest.mark.parametrize("code", [28, 56])
def test_proxy_route_mention_does_not_misclassify_other_errors(code):
    transport = failing_transport(curl_errors.ConnectionError(
        "Failed to connect to sts.cuhk.edu.cn:443 over proxy 127.0.0.1 "
        "after 0 ms: Could not connect to server", code=code,
    ))
    with pytest.raises(TransportError) as caught:
        transport.request("GET", SENSITIVE_URL)
    assert "无法连接代理" not in str(caught.value)


def test_unknown_transport_error_does_not_expose_exception_or_url():
    original = RuntimeError("password=private-password")
    transport = failing_transport(original)
    with pytest.raises(TransportError) as caught:
        transport.request("GET", "https://[malformed?code=authorization-secret")
    assert "RuntimeError" in str(caught.value)
    assert "authorization-secret" not in str(caught.value)
    assert "private-password" not in str(caught.value)
    assert caught.value.__cause__ is original


def test_download_connection_failure_uses_safe_diagnostic_without_retry(tmp_path):
    original = curl_errors.ConnectionError("code=authorization-secret", code=7)
    transport = failing_transport(original)
    destination = tmp_path / "slides.pdf"
    destination.write_bytes(b"previous download")
    with pytest.raises(TransportError) as caught:
        transport.download_to(SENSITIVE_URL, str(destination))
    assert "无法建立网络连接" in str(caught.value)
    assert "curl 7" in str(caught.value)
    assert "authorization-secret" not in str(caught.value)
    assert "private-password" not in str(caught.value)
    assert caught.value.__cause__ is original
    assert destination.read_bytes() == b"previous download"
    assert len(transport._sess.calls) == 1


def test_download_http_failure_does_not_expose_signed_url(tmp_path):
    transport = failing_transport(None)
    transport._sess = SimpleNamespace(
        curl_options={}, get=lambda *args, **kwargs: SimpleNamespace(status_code=403),
    )
    with pytest.raises(TransportError) as caught:
        transport.download_to(SENSITIVE_URL, str(tmp_path / "slides.pdf"))
    assert "403" in str(caught.value)
    assert "sts.cuhk.edu.cn:443" in str(caught.value)
    assert "authorization-secret" not in str(caught.value)
    assert "private-password" not in str(caught.value)


class RoutingSession:
    def __init__(self):
        self.calls = []
        self.curl_options = {CurlOpt.CONNECTTIMEOUT_MS: 4321}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs, dict(self.curl_options)))
        return SimpleNamespace(status_code=200, headers={}, text="ok", url=url,
                               iter_content=lambda **kwargs: iter([b"lecture slides"]))

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)


def route_sequence(monkeypatch, values):
    remaining = iter(values)
    urls = []

    def resolve(url):
        urls.append(url)
        return next(remaining)

    monkeypatch.setattr(transport_module, "resolve_proxy", resolve, raising=False)
    return urls


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("proxy", ["http://127.0.0.1:7890", "socks5h://127.0.0.1:7891"])
def test_same_session_switches_proxy_direct_proxy_before_each_request(monkeypatch, method, proxy):
    decisions = [proxy, "", proxy]
    urls = route_sequence(monkeypatch, decisions)
    transport = failing_transport(None)
    transport._sess = RoutingSession()
    data = {"Password": "credential-secret"} if method == "POST" else None

    for _ in decisions:
        assert transport.request(method, SENSITIVE_URL, data=data).status == 200

    assert urls == [SENSITIVE_URL] * 3
    assert len(transport._sess.calls) == 3
    for decision, (actual_method, url, kwargs, options) in zip(decisions, transport._sess.calls):
        assert actual_method == method
        assert url == SENSITIVE_URL
        assert kwargs["data"] == data
        assert kwargs["proxy"] == (decision or None)
        assert options[CurlOpt.PROXY] == decision
        assert options[CurlOpt.CONNECTTIMEOUT_MS] == 4321


def test_direct_decision_explicitly_overrides_environment_proxy(monkeypatch):
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    monkeypatch.setenv("all_proxy", "socks5h://127.0.0.1:7891")
    transport = failing_transport(None)
    transport._sess = RoutingSession()
    transport.request("GET", SENSITIVE_URL)
    _, _, kwargs, options = transport._sess.calls[0]
    assert kwargs["proxy"] is None
    assert options[CurlOpt.PROXY] == ""  # proxy=None alone still inherits curl's environment.


def test_same_session_switches_proxy_direct_proxy_for_downloads(monkeypatch, tmp_path):
    decisions = ["http://127.0.0.1:7890", "", "http://127.0.0.1:7890"]
    urls = route_sequence(monkeypatch, decisions)
    transport = failing_transport(None)
    transport._sess = RoutingSession()

    for index, decision in enumerate(decisions):
        destination = tmp_path / f"slides-{index}.pdf"
        assert transport.download_to(SENSITIVE_URL, str(destination)) == len(b"lecture slides")
        assert destination.read_bytes() == b"lecture slides"
        method, url, kwargs, options = transport._sess.calls[index]
        assert method == "GET"
        assert url == SENSITIVE_URL
        assert kwargs["stream"] is True
        assert kwargs["proxy"] == (decision or None)
        assert options[CurlOpt.PROXY] == decision
    assert urls == [SENSITIVE_URL] * 3
    assert len(transport._sess.calls) == 3


def test_proxy_failure_does_not_replay_credential_post_on_direct_connection(monkeypatch):
    urls = route_sequence(monkeypatch, ["http://127.0.0.1:7890", ""])
    error = curl_errors.ConnectionError("Could not connect to server", code=7)
    transport = failing_transport(error)

    with pytest.raises(TransportError) as caught:
        transport.request("POST", SENSITIVE_URL, data={"Password": "credential-secret"})

    assert caught.value.__cause__ is error
    assert urls == [SENSITIVE_URL]
    assert len(transport._sess.calls) == 1
    assert transport._sess.calls[0][2]["proxy"] == "http://127.0.0.1:7890"
    assert transport._sess.curl_options[CurlOpt.PROXY] == "http://127.0.0.1:7890"
