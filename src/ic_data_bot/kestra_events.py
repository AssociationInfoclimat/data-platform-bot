from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

MAX_EVENTS = 600
MAX_AGE = timedelta(hours=48)
MAX_EVENT_CHARS = 300
MAX_OUTPUT_CHARS = 6000


class KestraEventLog:
    """Cache mémoire des notifications Kestra relayées dans Discord
    (#system-events = succès, #alerts = échecs).

    L'infra data est isolée : Discord sert de bus d'événements. Le bot écoute
    ces canaux (+ backfill au démarrage) et l'outil kestra_recent lit ce cache
    — fenêtre glissante ~48 h, au-delà c'est l'UI Kestra qui fait foi.
    """

    def __init__(self, now: Callable[[], datetime] | None = None):
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._events: list[tuple[datetime, str, str]] = []  # (ts, source, texte)

    def record(self, ts: datetime, source: str, text: str) -> None:
        text = " ".join((text or "").split())[:MAX_EVENT_CHARS]
        if not text:
            return
        self._events.append((ts, source, text))
        self._events.sort(key=lambda e: e[0])
        self._prune()

    def _prune(self) -> None:
        cutoff = self._now() - MAX_AGE
        self._events = [e for e in self._events if e[0] >= cutoff][-MAX_EVENTS:]

    def count(self) -> int:
        self._prune()
        return len(self._events)

    @staticmethod
    def _age(delta: timedelta) -> str:
        s = max(0, int(delta.total_seconds()))
        if s < 90:
            return f"il y a {s} s"
        if s < 5400:
            return f"il y a {s // 60} min"
        return ("il y a %.1f h" % (s / 3600)).replace(".", ",")

    def recent(self, query: str = "", limit: int = 25) -> str:
        self._prune()
        if not self._events:
            return (
                "Aucun événement Kestra en cache (fenêtre 48 h). ATTENTION : "
                "silence ≠ tout va bien — le bot vient peut-être de démarrer, ou "
                "les notifications Discord de Kestra sont en panne. À signaler "
                "comme incertitude."
            )
        needle = (query or "").strip().lower()
        matches = (
            [e for e in self._events if needle in e[2].lower()] if needle else list(self._events)
        )
        if not matches:
            return (
                f"Aucun des {len(self._events)} événements des dernières 48 h ne "
                f"mentionne « {query} ». Soit le flow n'a pas tourné, soit il ne "
                "notifie pas Discord — à signaler comme incertitude, pas comme panne."
            )
        now = self._now()
        lines = [
            f"{len(matches)} événement(s) sur 48 h (plus récents d'abord, "
            f"{len(self._events)} au total en cache) :"
        ]
        for ts, source, text in reversed(matches[-limit:]):
            lines.append(f"[{self._age(now - ts)} · {source}] {text}")
        body = "\n".join(lines)[:MAX_OUTPUT_CHARS]
        return body + (
            "\n\n⚠️ Pour juger la fraîcheur d'une donnée : croise le dernier succès "
            "ci-dessus avec la règle `quality: freshness` du contrat concerné "
            "(read_file) avant de conclure."
        )
