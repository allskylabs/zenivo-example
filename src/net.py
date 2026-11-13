"""Shared outbound HTTP(S) helpers.

Building an SSL context loads the system CA bundle (ssl.load_default_certs), which is
expensive on a Raspberry Pi. We create it once and reuse it for every request, and keep
a small urllib3 pool so repeated GETs to the same host reuse connections.
Certificate verification stays ON.
"""
from __future__ import annotations

import ssl
import threading
import urllib.request
from typing import Any, Dict, Optional, Tuple, Union

_lock = threading.RLock()  # _get_pool() calls ssl_context() while holding it
_ctx: Optional[ssl.SSLContext] = None
_pool: Any = None


def ssl_context() -> ssl.SSLContext:
    global _ctx
    if _ctx is None:
        with _lock:
            if _ctx is None:
                _ctx = ssl.create_default_context()
    return _ctx


def https_handler() -> urllib.request.HTTPSHandler:
    """HTTPS handler bound to the shared context, for build_opener()."""
    return urllib.request.HTTPSHandler(context=ssl_context())


def urlopen(req: Union[str, urllib.request.Request], timeout: float):
    """urllib.request.urlopen with the shared SSL context (context only matters for https)."""
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())


def _get_pool():
    """Shared urllib3 pool, or None when urllib3 is not installed (then pooled_get falls back to urllib)."""
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                try:
                    import urllib3
                    _pool = urllib3.PoolManager(num_pools=8, maxsize=4, ssl_context=ssl_context())
                except ImportError:
                    _pool = False
    return _pool or None


def pooled_get(url: str, headers: Dict[str, str], timeout: float) -> Tuple[int, bytes, Dict[str, str]]:
    """GET through a shared keep-alive pool. Raises on HTTP status >= 400 (like urllib)."""
    pool = _get_pool()
    if pool is None:  # no urllib3: one connection per request, still with the shared SSL context
        with urlopen(urllib.request.Request(url, headers=headers), timeout) as r:
            return r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
    import urllib3
    resp = pool.request("GET", url, headers=headers, timeout=urllib3.Timeout(total=timeout),
                        retries=False, preload_content=True, decode_content=True)
    if resp.status >= 400:
        raise OSError(f"HTTP Error {resp.status} for {url}")
    return resp.status, resp.data, {k.lower(): v for k, v in resp.headers.items()}
