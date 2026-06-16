"""Client OAuth2 minimal des APIs Météo-France (auth portail-api + probe de disponibilité).

Miroir Python du client TypeScript `getMF()` de
telechargement-climatologie-portail-api-meteofrance : token bearer caché en mémoire
(TTL ~1 h), refresh-on-401, via `urllib.request` (cohérent avec github_issues._req).

N'expose QUE de quoi prober la disponibilité d'un endpoint (Range bytes=0-0, lecture
plafonnée) — il ne télécharge JAMAIS de gros payload (GRIB…) et ne renvoie jamais le
token ni l'APPLICATION_ID. L'APPLICATION_ID vient de l'environnement
(METEOFRANCE_APPLICATION_ID, alias MF_APPLICATION_ID), jamais committé.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

TOKEN_URL = "https://portail-api.meteofrance.fr/token"
_MAX_PROBE_BYTES = 2048
_BINARY_HINTS = ("grib", "octet-stream", "gzip", "x-tar", "image/")


class MeteoFranceError(Exception):
    """Échec d'auth / d'appel Météo-France (message sûr, sans secret)."""


def _snippet(body: bytes, content_type: str) -> str:
    ct = (content_type or "").lower()
    if any(h in ct for h in _BINARY_HINTS):
        return f"<binaire {len(body)} octets ({content_type})>"
    try:
        text = body.decode("utf-8", errors="replace").strip().replace("\n", " ")
    except Exception:
        return f"<{len(body)} octets>"
    return text[:200] + ("…" if len(text) > 200 else "")


class MeteoFranceAuth:
    """Gère le bearer token (cache + refresh) et probe des endpoints en lecture plafonnée."""

    def __init__(self, application_id: str, *, timeout: int = 15, clock=time.monotonic):
        self._app_id = (application_id or "").strip()
        self._timeout = timeout
        self._clock = clock
        self._lock = threading.Lock()
        self._token = ""
        self._expiry = 0.0

    # ── Token ────────────────────────────────────────────────────────────────
    def _fetch_token(self) -> str:
        if not self._app_id:
            raise MeteoFranceError("METEOFRANCE_APPLICATION_ID non configuré")
        req = urllib.request.Request(
            TOKEN_URL,
            data=b"grant_type=client_credentials",
            method="POST",
            headers={
                "Authorization": f"Basic {self._app_id}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "ic-data-bot",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                payload = json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            raise MeteoFranceError(f"échec token portail-api (HTTP {exc.code})") from exc
        except urllib.error.URLError as exc:
            raise MeteoFranceError(f"portail-api injoignable : {exc.reason}") from exc
        token = payload.get("access_token") or ""
        if not token:
            raise MeteoFranceError("réponse token sans access_token")
        ttl = float(payload.get("expires_in") or 3600)
        self._token = token
        self._expiry = self._clock() + max(60.0, ttl - 60.0)  # marge anti-expiration
        return token

    def token(self) -> str:
        with self._lock:
            if self._token and self._clock() < self._expiry:
                return self._token
            return self._fetch_token()

    def _invalidate(self) -> None:
        with self._lock:
            self._token = ""
            self._expiry = 0.0

    # ── Probe ────────────────────────────────────────────────────────────────
    def probe(self, url: str, *, auth: bool = True, range_probe: bool = True) -> dict:
        """GET léger (Range bytes=0-0, lecture ≤2 Ko). Refresh-on-401 (1 retry).

        Renvoie {status, content_type, length, snippet[, error]} — jamais le token."""
        last: dict = {"status": 0, "content_type": "", "length": "", "snippet": "échec inattendu", "error": True}
        for attempt in range(2):
            headers = {"User-Agent": "ic-data-bot"}
            if auth:
                headers["Authorization"] = f"Bearer {self.token()}"
            if range_probe:
                headers["Range"] = "bytes=0-0"
            req = urllib.request.Request(url, method="GET", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    body = resp.read(_MAX_PROBE_BYTES)
                    ct = resp.headers.get("Content-Type", "")
                    return {
                        "status": getattr(resp, "status", resp.getcode()),
                        "content_type": ct,
                        "length": resp.headers.get("Content-Range") or resp.headers.get("Content-Length") or "",
                        "snippet": _snippet(body, ct),
                    }
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and auth and attempt == 0:
                    self._invalidate()
                    continue
                try:
                    body = exc.read(_MAX_PROBE_BYTES)
                except Exception:
                    body = b""
                ct = exc.headers.get("Content-Type", "") if exc.headers else ""
                return {
                    "status": exc.code,
                    "content_type": ct,
                    "length": "",
                    "snippet": _snippet(body, ct),
                    "error": True,
                }
            except urllib.error.URLError as exc:
                last = {"status": 0, "content_type": "", "length": "",
                        "snippet": f"injoignable : {exc.reason}", "error": True}
                break
        return last
