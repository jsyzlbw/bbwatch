"""Proxy routing reads current settings and only probes configured local endpoints."""

import socket
import urllib.request
from unittest.mock import MagicMock, Mock

import pytest

from bbwatch import proxy

TARGET = "https://bb.cuhk.edu.cn/learn/api/public/v1/users/me"


@pytest.fixture
def routing(monkeypatch):
    environment = Mock(return_value={})
    system = Mock(return_value={})
    bypass = Mock(return_value=False)
    connection = MagicMock()
    connect = Mock(return_value=connection)
    monkeypatch.setattr(urllib.request, "getproxies_environment", environment)
    monkeypatch.setattr(proxy, "_system_proxies", system)
    monkeypatch.setattr(urllib.request, "proxy_bypass_macosx_sysconf", bypass, raising=False)
    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr(socket, "getaddrinfo", Mock(side_effect=AssertionError("No DNS probes")))
    monkeypatch.setattr(urllib.request, "urlopen", Mock(side_effect=AssertionError("No HTTP probes")))
    return environment, system, bypass, connect, connection


def test_no_configured_proxy_returns_explicit_direct(routing):
    _, _, _, connect, _ = routing
    assert proxy.resolve_proxy(TARGET) == ""
    connect.assert_not_called()


def test_environment_scheme_route_wins_over_all_and_system(routing):
    environment, system, bypass, connect, _ = routing
    environment.return_value = {
        "https": "https://secure-proxy.example:8443",
        "all": "http://all-proxy.example:8080",
    }
    system.return_value = {"https": "http://system-proxy.example:8080"}
    assert proxy.resolve_proxy(TARGET) == "https://secure-proxy.example:8443"
    bypass.assert_not_called()
    connect.assert_not_called()


def test_environment_all_route_wins_over_system(routing):
    environment, system, _, connect, _ = routing
    environment.return_value = {"all": "socks5h://remote-proxy.example:1080"}
    system.return_value = {"https": "http://system-proxy.example:8080"}
    assert proxy.resolve_proxy(TARGET) == "socks5h://remote-proxy.example:1080"
    connect.assert_not_called()


def test_system_route_fills_missing_environment_scheme(routing):
    environment, system, bypass, connect, _ = routing
    environment.return_value = {"http": "http://plain-proxy.example:8080"}
    system.return_value = {"https": "http://system-proxy.example:8080"}
    assert proxy.resolve_proxy(TARGET) == "http://system-proxy.example:8080"
    bypass.assert_called_once()
    connect.assert_not_called()


def test_local_proxy_stopping_and_starting_changes_route_without_restart(routing):
    environment, _, _, connect, connection = routing
    configured = "http://127.0.0.1:7890"
    environment.return_value = {"https": configured}
    connect.side_effect = [connection, ConnectionRefusedError("stopped"), connection]

    assert proxy.resolve_proxy(TARGET) == configured
    assert proxy.resolve_proxy(TARGET) == ""
    assert proxy.resolve_proxy(TARGET) == configured

    assert environment.call_count == 3
    assert connect.call_count == 3
    for call in connect.call_args_list:
        assert call.args[0] == ("127.0.0.1", 7890)
        assert 0 < call.kwargs["timeout"] <= 1
    assert connection.__exit__.call_count == 2


@pytest.mark.parametrize("configured,address", [
    ("http://localhost", ("localhost", 80)),
    ("https://127.0.0.2", ("127.0.0.2", 443)),
    ("socks5h://[::1]", ("::1", 1080)),
])
def test_local_proxy_addresses_and_default_ports_are_probed(routing, configured, address):
    environment, _, _, connect, connection = routing
    environment.return_value = {"https": configured}
    assert proxy.resolve_proxy(TARGET) == configured
    assert connect.call_args.args[0] == address
    assert 0 < connect.call_args.kwargs["timeout"] <= 1
    connection.__exit__.assert_called_once()


def test_unresponsive_local_proxy_returns_direct_after_bounded_probe(routing):
    environment, _, _, connect, _ = routing
    environment.return_value = {"https": "http://localhost:7890"}
    connect.side_effect = TimeoutError("proxy did not accept connection")
    assert proxy.resolve_proxy(TARGET) == ""
    connect.assert_called_once()
    assert 0 < connect.call_args.kwargs["timeout"] <= 1


def test_remote_proxy_is_preserved_without_connectivity_or_dns_probe(routing):
    environment, _, _, connect, _ = routing
    # A name containing localhost is not itself a loopback endpoint.
    configured = "http://user:secret@localhost.proxy.example:7890"
    environment.return_value = {"https": configured}
    connect.side_effect = AssertionError("Remote proxy must not be probed or bypassed")
    assert proxy.resolve_proxy(TARGET) == configured
    connect.assert_not_called()


def test_no_proxy_exclusion_applies_before_environment_or_system_routes(routing):
    environment, system, _, connect, _ = routing
    environment.return_value = {"no": ".cuhk.edu.cn"}
    system.return_value = {"https": "http://127.0.0.1:7890"}
    assert proxy.resolve_proxy(TARGET) == ""
    environment.return_value["https"] = "http://127.0.0.1:7890"
    assert proxy.resolve_proxy(TARGET) == ""
    connect.assert_not_called()


def test_system_exclusions_are_respected(routing):
    _, system, bypass, connect, _ = routing
    system.return_value = {"https": "http://127.0.0.1:7890"}
    bypass.return_value = True
    assert proxy.resolve_proxy(TARGET) == ""
    bypass.assert_called_once()
    connect.assert_not_called()


def test_invalid_proxy_configuration_raises_without_silent_direct_route(routing):
    environment, _, _, connect, _ = routing
    for configured in ("http://localhost:notaport", "http://:7890", "file:///tmp/proxy"):
        environment.return_value = {"https": configured}
        with pytest.raises(ValueError):
            proxy.resolve_proxy(TARGET)
    connect.assert_not_called()


def test_current_macos_socks_proxy_is_normalized_and_reread(routing):
    _, system, _, connect, connection = routing
    # macOS stdlib exposes the SOCKS endpoint with an http URL under the socks key.
    system.side_effect = [{"socks": "http://localhost:7891"}, {}]
    assert proxy.resolve_proxy(TARGET) == "socks5h://localhost:7891"
    assert proxy.resolve_proxy(TARGET) == ""
    assert system.call_count == 2
    assert connect.call_args.args[0] == ("localhost", 7891)
    connection.__exit__.assert_called_once()
