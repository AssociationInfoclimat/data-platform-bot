from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from datetime import date
from pathlib import Path
from typing import Callable, Deque, Dict


def is_allowed_channel(channel_id: int, allowed_channel_id: int) -> bool:
    return channel_id == allowed_channel_id


class RateLimiter:
    """Fenêtre glissante par utilisateur."""

    def __init__(self, max_events: int, window_seconds: int, clock: Callable[[], float] = time.monotonic):
        self.max_events = max_events
        self.window = window_seconds
        self.clock = clock
        self._events: Dict[str, Deque[float]] = defaultdict(deque)

    def allow(self, user_id: str) -> bool:
        now = self.clock()
        q = self._events[user_id]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.max_events:
            return False
        q.append(now)
        return True


class DailyBudget:
    """Plafond quotidien de tokens, persistant en JSON, reset au changement de jour."""

    def __init__(self, path: Path, limit: int, today_fn: Callable[[], str] = lambda: date.today().isoformat()):
        self.path = Path(path)
        self.limit = limit
        self.today_fn = today_fn
        self._date, self._used = self._load()

    def _load(self) -> tuple[str, int]:
        today = self.today_fn()
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text())
                if data.get("date") == today:
                    return today, int(data.get("tokens", 0))
            except (json.JSONDecodeError, OSError, ValueError):
                pass
        return today, 0

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"date": self._date, "tokens": self._used}))

    def _roll(self) -> None:
        today = self.today_fn()
        if today != self._date:
            self._date, self._used = today, 0
            self._save()

    def remaining(self) -> int:
        self._roll()
        return max(0, self.limit - self._used)

    def has_budget(self) -> bool:
        return self.remaining() > 0

    def add(self, tokens: int) -> None:
        self._roll()
        self._used += max(0, tokens)
        self._save()
