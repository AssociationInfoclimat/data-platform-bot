#!/usr/bin/env python
"""Lance les évals (docs/evals/questions.md) HORS Discord, contre l'agent du ou
des provider(s) dont la clé est présente (→ comparaison Mistral vs Haiku
automatique si les deux clés sont là). Inclut un backfill Kestra pour E5.

À lancer dans le conteneur (où vivent snapshot + deps + .env) :

    docker compose exec -T bot uv run python scripts/run_eval.py            # toutes les évals
    docker compose exec -T bot uv run python scripts/run_eval.py E1 E3      # évals choisies
    docker compose exec -T bot uv run python scripts/run_eval.py -q "ma question libre"

Modèles : le provider actif (PROVIDER) utilise MODEL ; l'autre provider, s'il a
une clé, utilise ANTHROPIC_MODEL / MISTRAL_MODEL (défauts ci-dessous).
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from ic_data_bot.bot import install_ops_overlay
from ic_data_bot.config import load_config
from ic_data_bot.context import build_system_blocks
from ic_data_bot.kestra_events import KestraEventLog
from ic_data_bot.tools import ToolBox

EVALS = {
    "E1": "dans la table foudre, que contient exactement la colonne dh_usec ? Il y a un piège ?",
    "E2": "quels contrats ODCS existent et lesquels sont en draft ?",
    "E3": ("on envisage de décommissionner la table foudre (V5) pendant la migration : "
           "qu'est-ce qui casse en aval, et qui écrit encore dedans ?"),
    "E4": "sur quel hôte tourne TimescaleDB et c'est quoi son IP ?",
    "E5": "la climato est à jour ?",
}
DEFAULT_MODEL = {"anthropic": "claude-haiku-4-5", "mistral": "mistral-small-latest"}


def _discord_get(url: str, token: str, tries: int = 4):
    """GET REST Discord avec retry sur 429 (urllib brut ne le gère pas)."""
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bot {token}", "User-Agent": "ic-data-bot-eval"})
    for attempt in range(tries):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=15).read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < tries - 1:
                wait = float(exc.headers.get("Retry-After", "1")) + 0.5
                time.sleep(min(wait, 10))
                continue
            raise


def backfill_kestra(cfg, log: KestraEventLog) -> None:
    """Reproduit le cache d'événements Kestra (lecture REST des 2 canaux)."""
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    chans = [(cfg.kestra_events_channel_id, "system-events"),
             (cfg.kestra_alerts_channel_id, "alerts")]
    for cid, label in chans:
        if not cid or not token:
            continue
        try:
            # limit max Discord = 100 (le bot live pagine via discord.py ; ici
            # 100 récents/canal suffisent largement pour la fenêtre 48 h).
            msgs = _discord_get(
                f"https://discord.com/api/v10/channels/{cid}/messages?limit=100", token)
        except Exception as exc:
            print(f"[eval] backfill #{label} KO : {type(exc).__name__}")
            continue
        for m in msgs:
            text = m.get("content", "")
            for e in m.get("embeds", []):
                text += " | " + (e.get("title") or "") + " " + (e.get("description") or "")
            ts = datetime.datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
            log.record(ts, label, text)


def build_agents(cfg, system_blocks, toolbox, mistral_models=None) -> dict:
    """Un agent par provider dont la clé est présente. `mistral_models` (liste)
    permet d'A/B plusieurs modèles Mistral (ex. mistral-small vs magistral-small)."""
    agents = {}
    # En A/B Mistral focalisé (--mistral-models), on n'ajoute pas Haiku.
    if cfg.anthropic_api_key and not mistral_models:
        import anthropic
        from ic_data_bot.claude import DataManagerAgent
        model = cfg.model if cfg.provider == "anthropic" else \
            os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL["anthropic"]
        agents[f"anthropic:{model}"] = DataManagerAgent(
            anthropic.Anthropic(api_key=cfg.anthropic_api_key), model,
            cfg.max_tokens, system_blocks, toolbox)
    if cfg.mistral_api_key:
        from mistralai.client import Mistral
        from ic_data_bot.mistral import MistralAgent
        client = Mistral(api_key=cfg.mistral_api_key)
        models = mistral_models or [
            cfg.model if cfg.provider == "mistral"
            else os.environ.get("MISTRAL_MODEL") or DEFAULT_MODEL["mistral"]]
        for model in models:
            agents[f"mistral:{model}"] = MistralAgent(
                client, model, cfg.max_tokens, system_blocks, toolbox)
    return agents


def main(argv: list[str]) -> int:
    cfg = load_config(os.environ)
    snap = Path(cfg.snapshot_dir)
    install_ops_overlay(snap, cfg.ops_mapping_path)
    kestra_log = KestraEventLog()
    backfill_kestra(cfg, kestra_log)
    toolbox = ToolBox(snap, max_bytes=cfg.tool_read_max_bytes, kestra_log=kestra_log)
    system_blocks = build_system_blocks(snap)
    mistral_models = None
    if "--mistral-models" in argv:
        mistral_models = argv[argv.index("--mistral-models") + 1].split(",")
    agents = build_agents(cfg, system_blocks, toolbox, mistral_models)
    if not agents:
        print("Aucune clé provider — rien à lancer."); return 1
    print(f"Providers : {', '.join(agents)}  |  Kestra : {kestra_log.count()} évts\n")

    # Sélection des questions
    if "-q" in argv:
        q = argv[argv.index("-q") + 1]
        items = [("ad-hoc", q)]
    else:
        ids = [a.upper() for a in argv if a.upper() in EVALS] or list(EVALS)
        items = [(i, EVALS[i]) for i in ids]

    for eid, question in items:
        print("=" * 70)
        print(f"[{eid}] {question}")
        for name, agent in agents.items():
            t0 = time.monotonic()
            try:
                r = agent.answer(question, history=[])
                dur = time.monotonic() - t0
                print(f"\n--- {name}  (iters={r.iterations}, tokens={r.tokens}, {dur:.1f}s) ---")
                print(r.text)
            except Exception as exc:
                print(f"\n--- {name} : ERREUR {type(exc).__name__}: {exc} ---")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
