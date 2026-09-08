"""Read current proxy settings; a stopped local proxy permits direct connections."""
import ipaddress
import socket
import urllib.request
from urllib.parse import urlsplit

_DEFAULT_PORTS = {
    "http": 80, "https": 443,
    "socks4": 1080, "socks4a": 1080, "socks5": 1080, "socks5h": 1080,
}


def _system_proxies():
    reader = getattr(urllib.request, "getproxies_macosx_sysconf", None)
    return reader() if reader is not None else {}


def _loopback(host: str) -> bool:
    if host.rstrip(".") == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host)
        return address.is_loopback or bool(
            isinstance(address, ipaddress.IPv6Address)
            and address.ipv4_mapped and address.ipv4_mapped.is_loopback
        )
    except ValueError:
        return False


def resolve_proxy(url: str) -> str:
    """Resolve each request afresh without modifying global environment or retrying HTTP."""
    target = urlsplit(url)
    environment = urllib.request.getproxies_environment()
    host = target.netloc.rsplit("@", 1)[-1]
    if urllib.request.proxy_bypass_environment(host, environment):
        return ""
    route = environment.get(target.scheme) or environment.get("all")
    if not route:
        system = _system_proxies()
        route = system.get(target.scheme) or system.get("all")
        if not route and system.get("socks"):
            raw = system["socks"]
            route = "socks5h://" + (raw.split("://", 1)[-1])
        if route:
            bypass = getattr(urllib.request, "proxy_bypass_macosx_sysconf", None)
            if bypass is not None and bypass(host):
                return ""
    if not route:
        return ""
    route = route if "://" in route else "http://" + route
    try:
        parsed = urlsplit(route)
        if parsed.scheme not in _DEFAULT_PORTS or not parsed.hostname:
            raise ValueError
        port = parsed.port if parsed.port is not None else _DEFAULT_PORTS[parsed.scheme]
        if port <= 0:
            raise ValueError
    except ValueError:
        raise ValueError("代理配置无效，请检查协议、地址和端口。") from None
    if _loopback(parsed.hostname):
        try:
            # Only probe the configured loopback endpoint, never send HTTP credentials.
            with socket.create_connection((parsed.hostname, port), timeout=0.3):
                pass
        except OSError:
            return ""
    return route
