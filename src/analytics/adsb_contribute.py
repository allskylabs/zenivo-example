"""
Contribute what the owner's own ADS-B receiver hears to the Zenivo community network (opt-in, off by default).

Only aircraft decoded by the owner's receiver (AdsbFeed) are ever sent. The class holds a reference to the receiver
feed and nothing else: it cannot reach the public-network feed or Flightradar24, whose terms forbid re-sharing, and
it refuses any feed that is not an AdsbFeed. Running needs all of: the switch on, a network base URL (origin of the
configured remote solver), and a receiver that is delivering. The station key is the community identity, so this works
while community sharing is off, without announcing the station.

Wire: POST {base}/api/v1/adsb/ingest, JSON {"v":1,"now":<s>,"ac":[readsb-style rows]}, at most 300 aircraft, only
fixes newer than 30 s, one request per POST_S while there is something to send. Headers X-Zenivo-Id / -Key / -Time /
-Sig; the signature covers b"zenivo-adsb-v1\\n" + "<id>\\n<t>\\n<sha256 hex of the body>".
"""

import base64
import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from src.analytics.adsb import FT, AdsbFeed
from src.analytics.adsb_public import FetchError, _num, receiver_delivering
from src.net import urlopen

logger = logging.getLogger("imx335.adsb_contribute")

DOMAIN = b"zenivo-adsb-v1\n"
INGEST_PATH = "/api/v1/adsb/ingest"
POST_S = 5.0
MAX_AIRCRAFT = 300
MAX_FIX_AGE_S = 30.0
TIMEOUT_S = 8.0
BACKOFF_MAX_S = 300.0
RETRY_AFTER_MAX_S = 900.0
USER_AGENT = "zenivo-allsky"


def build_payload(feed: AdsbFeed, now: float) -> Optional[Dict[str, Any]]:
    """The ingest body for the receiver's current aircraft, or None when none has a fresh position fix."""
    rows: List[Dict[str, Any]] = []
    for r in feed.latest_fixes(MAX_FIX_AGE_S, at=now)[:MAX_AIRCRAFT]:
        row: Dict[str, Any] = {"hex": r["icao"].lower(), "lat": round(r["lat"], 5), "lon": round(r["lon"], 5),
                               "alt_baro": "ground" if r["alt_m"] <= 0 else int(round(r["alt_m"] / FT)),
                               "seen_pos": round(max(0.0, now - r["t"]), 1)}
        if r.get("callsign"):
            row["flight"] = r["callsign"]
        for key, src in (("gs", "speed_kt"), ("track", "track_deg"), ("baro_rate", "vrate_fpm")):
            if src in r:
                row[key] = round(r[src], 1) if key != "baro_rate" else int(round(r[src]))
        rows.append(row)
    return {"v": 1, "now": round(now, 1), "ac": rows} if rows else None


def sign_ingest(identity: Any, body: bytes, t: int) -> Dict[str, str]:
    """Request headers proving the holder of the station key sent exactly `body` at second `t`."""
    msg = DOMAIN + f"{identity.id}\n{int(t)}\n{hashlib.sha256(body).hexdigest()}".encode("ascii")
    return {"X-Zenivo-Id": identity.id, "X-Zenivo-Key": base64.b64encode(identity.pubkey).decode("ascii"),
            "X-Zenivo-Time": str(int(t)), "X-Zenivo-Sig": base64.b64encode(identity.sign(msg)).decode("ascii"),
            "Content-Type": "application/json", "User-Agent": USER_AGENT}


def http_post(url: str, body: bytes, headers: Dict[str, str]) -> Any:
    """POST `body`, parse the JSON answer; raises FetchError (with Retry-After) on HTTP errors."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=TIMEOUT_S) as r:
            raw = r.read(65536)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = str(json.loads(exc.read(4096).decode("utf-8")).get("detail", ""))[:80]
        except Exception:
            pass
        ra = _num(exc.headers.get("Retry-After") if exc.headers else None, None)
        raise FetchError(f"HTTP {exc.code}" + (f": {detail}" if detail else ""), ra) from None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return {}


class AdsbContributor:
    """One daemon thread; posts the receiver's aircraft every POST_S seconds while it is allowed to."""

    def __init__(self, receiver: Callable[[], Optional[AdsbFeed]], enabled: Callable[[], bool],
                 base: Callable[[], str], identity: Callable[[], Any],
                 post: Callable[[str, bytes, Dict[str, str]], Any] = http_post,
                 clock: Callable[[], float] = time.time):
        self._receiver, self._enabled, self._base, self._identity = receiver, enabled, base, identity
        self._post, self._clock = post, clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._fails = 0
        self.posts = 0
        self.last_ok_ts = 0.0
        self.last_error: Optional[str] = None
        self.last_sent = 0

    # ------------------------------------------------------------------ thread
    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="adsb-contribute", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        wait = 5.0
        while not self._stop.is_set():
            try:
                wait = self.step()
            except Exception:   # never leave the thread
                logger.debug("ADS-B contribute loop error", exc_info=True)
                wait = 30.0
            self._stop.wait(wait)

    # ------------------------------------------------------------------ one cycle
    def _feed(self) -> Optional[AdsbFeed]:
        """The receiver feed when it is delivering; anything that is not an AdsbFeed is refused."""
        feed = self._receiver()
        if isinstance(feed, AdsbFeed) and receiver_delivering(feed.status()):
            return feed
        return None

    def active(self) -> bool:
        try:
            return bool(self._enabled() and self._base() and self._feed() is not None)
        except Exception:
            return False

    def step(self) -> float:
        """One cycle; returns the seconds to wait before the next one."""
        if not self.active():
            self._fails = 0
            return POST_S
        now = self._clock()
        payload = build_payload(self._feed(), now)
        if payload is None:
            return POST_S
        ident = self._identity()
        if ident is None:
            self.last_error = "the cryptography package is missing: cannot sign"
            return BACKOFF_MAX_S
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            self._post(self._base() + INGEST_PATH, body, sign_ingest(ident, body, int(now)))
        except Exception as exc:
            self._fails += 1
            self.last_error = str(exc)[:100]
            delay = min(BACKOFF_MAX_S, POST_S * 2 ** self._fails)
            ra = getattr(exc, "retry_after_s", None)
            return min(RETRY_AFTER_MAX_S, max(delay, ra)) if ra is not None else delay
        self._fails, self.last_error, self.last_ok_ts = 0, None, now
        self.posts += 1
        self.last_sent = len(payload["ac"])
        return POST_S

    def status(self) -> Dict[str, Any]:
        return {"active": self.active(), "last_ok": self.last_ok_ts or None, "last_error": self.last_error,
                "last_sent": self.last_sent, "posts": self.posts}
