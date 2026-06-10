from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

MAX_ITEMS = 50
MAX_TEXT_CHARS = 500
MAX_BLOCK_CHARS = 8000


class CorrectionsStore:
    """Errata signalés par les devs via !fix — persistés en JSONL.

    Injectés comme bloc system APRÈS le breakpoint cache_control : les ajouts/
    retraits ne coûtent donc aucune invalidation du cache Anthropic. Un erratum
    est une rustine en attendant le correctif dans data-platform ; une fois le
    repo corrigé (et le snapshot rafraîchi), on le retire via !unfix.
    """

    def __init__(self, path: Path, max_items: int = MAX_ITEMS):
        self.path = Path(path)
        self.max_items = max_items
        self._items: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if not self.path.is_file():
            return []
        try:
            items = [
                json.loads(line)
                for line in self.path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (json.JSONDecodeError, OSError):
            return []
        return items[-self.max_items :]

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(json.dumps(i, ensure_ascii=False) for i in self._items)
        self.path.write_text(payload + "\n" if payload else "", encoding="utf-8")

    def add(self, author: str, text: str, ref_excerpt: str | None = None) -> int:
        item = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "author": author,
            "text": text[:MAX_TEXT_CHARS],
        }
        if ref_excerpt:
            item["ref"] = ref_excerpt[:150]
        self._items.append(item)
        del self._items[: max(0, len(self._items) - self.max_items)]
        self._save()
        return len(self._items)

    def remove(self, index: int) -> bool:
        """index 1-based, tel qu'affiché par !fixes."""
        if 1 <= index <= len(self._items):
            del self._items[index - 1]
            self._save()
            return True
        return False

    def items(self) -> list[dict]:
        return list(self._items)

    def render_system_block(self) -> dict | None:
        """Bloc system errata, ou None s'il n'y en a aucun."""
        if not self._items:
            return None
        lines = [
            "### Errata signalés par les développeurs — PRIORITAIRES sur le snapshot",
            "Ces corrections rectifient des erreurs connues du snapshot, en attendant "
            "leur correctif dans data-platform. En cas de contradiction entre un "
            "erratum et le snapshot, l'erratum fait foi ; signale alors explicitement "
            "que ta réponse s'appuie sur un erratum.",
        ]
        for i, it in enumerate(self._items, 1):
            ref = f" (au sujet de : « {it['ref']} … »)" if it.get("ref") else ""
            lines.append(f"{i}. [{it['ts'][:10]}, {it['author']}] {it['text']}{ref}")
        return {"type": "text", "text": "\n".join(lines)[:MAX_BLOCK_CHARS]}
