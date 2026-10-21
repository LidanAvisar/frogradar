"""SSRF guard for every URL a research mission fetches (D72).

Search results and redirects come from the web, so a URL may point at the machine FrogRadar runs on
or at its network (localhost, the Docker network, a cloud metadata endpoint at 169.254.169.254).
Rules: http/https only, no credentials in the URL, default ports only (80/443), and every address
the host name resolves to must be a public unicast address (not loopback, private, link-local,
multicast, reserved, unspecified, shared / carrier-grade NAT, or the IPv6 equivalents, incl.
IPv4-mapped IPv6). PoliteClient calls check_url before every request, so redirects are re-checked.

Known limit: the address is resolved here and again by the HTTP client (a DNS-rebinding host could
answer differently the second time). Acceptable for an analyst-run tool; pinning the resolved IP
would close it.
"""

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit

from app.pipeline.collectors.http import FetchError

Resolver = Callable[[str], list[str]]
ALLOWED_PORTS = frozenset({None, 80, 443})


class UnsafeUrl(FetchError):
    """The URL is not allowed for a mission fetch (scheme, port or address)."""


def system_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as e:
        raise UnsafeUrl(f"cannot resolve host {host!r}") from e
    return sorted({str(info[4][0]) for info in infos})


def blocked_reason(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None:
            return blocked_reason(mapped)
        if ip.teredo is not None:
            return "teredo address"
    for attr in ("is_loopback", "is_private", "is_link_local", "is_multicast", "is_reserved",
                 "is_unspecified"):  # fmt: skip
        if getattr(ip, attr):
            return attr.removeprefix("is_").replace("_", "-") + " address"
    if not ip.is_global:  # shared address space 100.64/10, documentation ranges, ...
        return "non-public address"
    return None


def check_url(url: str, resolve: Resolver = system_resolver) -> None:
    """Raise UnsafeUrl unless the URL is http(s) to a public address."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeUrl(f"scheme {parts.scheme or '(none)'!r} not allowed")
    if parts.username or parts.password:
        raise UnsafeUrl("credentials in URL not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeUrl("URL has no host")
    try:
        port = parts.port
    except ValueError as e:
        raise UnsafeUrl("invalid port") from e
    if port not in ALLOWED_PORTS:
        raise UnsafeUrl(f"port {port} not allowed")
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        if host.lower() == "localhost" or host.lower().endswith(".localhost"):
            raise UnsafeUrl(f"host {host!r} not allowed") from None
        addresses = resolve(host)
    if not addresses:
        raise UnsafeUrl(f"host {host!r} has no address")
    for a in addresses:
        try:
            ip = ipaddress.ip_address(a.split("%")[0])
        except ValueError as e:
            raise UnsafeUrl(f"bad address {a!r} for {host!r}") from e
        if (reason := blocked_reason(ip)) is not None:
            raise UnsafeUrl(f"{host} resolves to a {reason} ({ip}): blocked")
