"""SSRF guard and bounded JSON fetch for URLs that come from other stations.

URLs learned from peers are checked strictly: https only, no credentials, and every address the host resolves to
must be a public one. The connection then goes to the address that was checked (not a second DNS lookup, which a
hostile DNS server could answer differently), redirects are never followed and the response size is capped.
Only seed URLs the owner typed (allow_private=True) may use http or private addresses, so a LAN can be tested."""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
from typing import Any, List, Optional, Tuple
from urllib.parse import urlsplit

from src.net import ssl_context
from src.version import __version__

def _no_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


TIMEOUT_S = 8.0
MAX_RESPONSE = 512 * 1024
MAX_URL = 300


class UnsafeURL(ValueError):
    """The URL is not allowed to be contacted."""


_TRANSLATED = tuple(ipaddress.ip_network(n) for n in (
    "64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32"))  # NAT64, local NAT64, 6to4, Teredo


def _public(ip: Any) -> bool:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif any(ip in net for net in _TRANSLATED):
            return False  # an IPv4 address (possibly private) tunnelled inside IPv6: not worth the risk
        elif int(ip) >> 32 == 0 and int(ip) > 1:  # IPv4-compatible ::a.b.c.d
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
                or ip.is_unspecified) and ip.is_global


def resolve(url: str, allow_private: bool = False) -> Tuple[str, str, int, List[str], str]:
    """Validate `url`; return (scheme, host, port, checked addresses, path+query). Raises UnsafeURL."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL or any(c.isspace() or ord(c) < 32 for c in url):
        raise UnsafeURL("invalid URL")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise UnsafeURL("invalid URL")
    scheme = parts.scheme.lower()
    if scheme != "https" and not (scheme == "http" and allow_private):
        raise UnsafeURL("only https is allowed")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURL("credentials in URL")
    host = parts.hostname
    if not host:
        raise UnsafeURL("no host")
    port = port or (443 if scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise UnsafeURL("host does not resolve")
    addrs: List[str] = []
    for info in infos:
        text = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(text)
        except ValueError:
            raise UnsafeURL("bad address")
        if not allow_private and not _public(ip):
            raise UnsafeURL("address is not public")
        if text not in addrs:
            addrs.append(text)
    if not addrs:
        raise UnsafeURL("host does not resolve")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return scheme, host, port, addrs, path


def safe_url(url: str, allow_private: bool = False) -> str:
    """Return `url` when it may be contacted, else raise UnsafeURL."""
    resolve(url, allow_private)
    return url


def _connection(scheme: str, host: str, port: int, ip: str, timeout: float):
    """Connection to `host` that dials the already-checked `ip` (TLS still verifies the certificate for `host`)."""
    if scheme == "https":
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ssl_context())
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)

    def connect() -> None:
        sock = socket.create_connection((ip, port), timeout)
        conn.sock = ssl_context().wrap_socket(sock, server_hostname=host) if scheme == "https" else sock

    conn.connect = connect  # type: ignore[method-assign]
    return conn


def fetch_json(url: str, *, allow_private: bool = False, method: str = "GET", payload: Optional[Any] = None,
               timeout: float = TIMEOUT_S, max_bytes: int = MAX_RESPONSE) -> Any:
    """GET/POST `url` and return the decoded JSON body. Raises UnsafeURL or OSError/ValueError on any failure."""
    scheme, host, port, addrs, path = resolve(url, allow_private)
    body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json", "User-Agent": f"Zenivo/{__version__} community"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    last: Optional[Exception] = None
    for ip in addrs:
        conn = _connection(scheme, host, port, ip, timeout)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            if resp.status // 100 != 2:  # includes 3xx: redirects are not followed
                raise OSError(f"HTTP {resp.status}")
            if "json" not in (resp.getheader("Content-Type") or "").lower():
                raise ValueError("response is not JSON")
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("response is too large")
            return json.loads(data.decode("utf-8"), parse_constant=_no_constant)
        except (OSError, http.client.HTTPException) as exc:
            last = exc
        finally:
            conn.close()
    raise OSError(str(last) if last else "no address")
