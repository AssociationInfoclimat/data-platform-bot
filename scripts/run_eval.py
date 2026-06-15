#!/usr/bin/env python
"""Lance les évals (docs/evals/questions.md) HORS Discord, contre l'agent du ou
des provider(s) dont la clé est présente (→ comparaison Mistral vs Haiku
automatique si les deux clés sont là). Inclut un backfill Kestra pour E5.

À lancer dans le conteneur (où vivent snapshot + deps + .env) :

    docker compose exec -T bot uv run python scripts/run_eval.py            # toutes les évals
    docker compose exec -T bot uv run python scripts/run_eval.py E1 E3      # évals choisies
    docker compose exec -T bot uv run python scripts/run_eval.py -q "ma question libre"

Options : --grade (notation LLM-juge + matrice), --mistral-models a,b (A/B),
--langfuse (pousse dataset + traces + scores dans Langfuse, si clés présentes).
Modèles : le provider actif (PROVIDER) utilise MODEL ; l'autre provider, s'il a
une clé, utilise ANTHROPIC_MODEL / MISTRAL_MODEL (défauts ci-dessous).
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path

import yaml

from ic_data_bot.bot import install_ops_overlay
from ic_data_bot.config import load_config
from ic_data_bot.context import build_system_blocks, set_persona_source
from ic_data_bot.kestra_events import KestraEventLog
from ic_data_bot.telemetry import make_langfuse, redact, register_prompt, resolve_prompt
from ic_data_bot.tools import ToolBox

LANGFUSE_DATASET = "ic-data-bot-evals"

DATASET_PATH = Path(__file__).resolve().parent.parent / "docs" / "evals" / "dataset.yaml"
DEFAULT_MODEL = {"anthropic": "claude-haiku-4-5", "mistral": "mistral-small-latest"}

# Fallback si dataset.yaml absent : les 5 questions historiques, sans critères.
EVALS_FALLBACK = [
    {"id": "E1", "category": "pièges", "criteria": [],
     "question": "dans la table foudre, que contient exactement la colonne dh_usec ? Il y a un piège ?"},
    {"id": "E2", "category": "index", "criteria": [],
     "question": "quels contrats ODCS existent et lesquels sont en draft ?"},
    {"id": "E3", "category": "lineage", "criteria": [],
     "question": "on envisage de décommissionner la table foudre (V5) pendant la migration : qu'est-ce qui casse en aval, et qui écrit encore dedans ?"},
    {"id": "E4", "category": "ops", "criteria": [],
     "question": "sur quel hôte tourne TimescaleDB et c'est quoi son IP ?"},
    {"id": "E5", "category": "fraicheur", "criteria": [],
     "question": "la climato est à jour ?"},
]


def load_questions() -> list[dict]:
    """Charge dataset.yaml (id/category/question/criteria), sinon le fallback."""
    if DATASET_PATH.is_file():
        data = yaml.safe_load(DATASET_PATH.read_text(encoding="utf-8")) or {}
        return [
            {"id": q["id"], "category": q.get("category", "?"),
             "question": q["question"], "criteria": q.get("criteria", [])}
            for q in data.get("questions", [])
        ]
    return EVALS_FALLBACK


# ── Grader LLM-juge ──────────────────────────────────────────────────────────
JUDGE_SYS = (
    "Tu es un évaluateur rigoureux et impartial. On te donne une QUESTION posée à "
    "un assistant data, une liste numérotée de CRITÈRES (faits qui doivent figurer "
    "dans une bonne réponse) et la RÉPONSE de l'assistant. Pour chaque critère, dis "
    "s'il est rempli en te basant UNIQUEMENT sur la réponse — un critère compte comme "
    "rempli si la réponse l'exprime clairement, même avec d'autres mots. Réponds "
    "UNIQUEMENT par un objet JSON : "
    '{"criteres":[{"i":0,"met":true}, ...],"note":"<une phrase de justification>"}'
)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _judge_payload(question: str, criteria: list[str], answer: str) -> str:
    crit = "\n".join(f"{i}) {c}" for i, c in enumerate(criteria))
    return f"QUESTION :\n{question}\n\nCRITÈRES :\n{crit}\n\nRÉPONSE :\n{answer[:4000]}"


def _parse_verdict(text: str, total: int) -> tuple[int, str]:
    try:
        d = json.loads(_JSON_RE.search(text).group(0))
        met = sum(1 for c in d.get("criteres", []) if c.get("met"))
        return min(met, total), str(d.get("note", ""))[:200]
    except Exception:
        return 0, "(verdict illisible)"


def make_judge(cfg, judge_sys=JUDGE_SYS):
    """Retourne (judge(question, criteria, answer)->(met,total,note), label) ou (None, None).
    judge_sys = prompt système du juge (résolu depuis Langfuse, fallback code)."""
    spec = os.environ.get("EVAL_JUDGE", "")
    if spec:
        prov, _, model = spec.partition(":")
    elif cfg.anthropic_api_key:
        prov, model = "anthropic", DEFAULT_MODEL["anthropic"]
    elif cfg.mistral_api_key:
        prov, model = "mistral", DEFAULT_MODEL["mistral"]
    else:
        return None, None
    model = model or DEFAULT_MODEL.get(prov, "")

    if prov == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

        def judge(question, criteria, answer):
            r = client.messages.create(
                model=model, max_tokens=600, system=judge_sys,
                messages=[{"role": "user", "content": _judge_payload(question, criteria, answer)}])
            text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
            met, note = _parse_verdict(text, len(criteria))
            return met, len(criteria), note
    else:
        from mistralai.client import Mistral
        client = Mistral(api_key=cfg.mistral_api_key)

        def judge(question, criteria, answer):
            r = client.chat.complete(
                model=model, max_tokens=600,
                messages=[{"role": "system", "content": judge_sys},
                          {"role": "user", "content": _judge_payload(question, criteria, answer)}])
            content = r.choices[0].message.content
            text = content if isinstance(content, str) else \
                "".join(getattr(c, "text", "") or "" for c in (content or []))
            met, note = _parse_verdict(text, len(criteria))
            return met, len(criteria), note
    return judge, f"{prov}:{model}"


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


def sync_langfuse_dataset(lf, items) -> None:
    """Pousse les questions/critères comme dataset Langfuse (idempotent par id)."""
    try:
        lf.create_dataset(name=LANGFUSE_DATASET, description="Éval ancrée ic-data-bot")
    except Exception:
        pass
    for q in items:
        if not q.get("criteria"):
            continue
        try:
            lf.create_dataset_item(
                dataset_name=LANGFUSE_DATASET, id=f"icbot-{q['id']}",
                input={"question": q["question"]},
                expected_output="\n".join(q["criteria"]),
                metadata={"id": q["id"], "category": q["category"]})
        except Exception:
            pass


def main(argv: list[str]) -> int:
    cfg = load_config(os.environ)
    snap = Path(cfg.snapshot_dir)
    install_ops_overlay(snap, cfg.ops_mapping_path)
    kestra_log = KestraEventLog()
    backfill_kestra(cfg, kestra_log)
    toolbox = ToolBox(snap, max_bytes=cfg.tool_read_max_bytes, kestra_log=kestra_log)
    lf = make_langfuse(cfg) if "--langfuse" in argv else None
    # Prompt management : l'éval utilise le MÊME persona 'production' que la prod
    # (Langfuse, fallback code), et versionne + résout le prompt du juge.
    set_persona_source(lf)
    register_prompt(lf, "ic-data-bot-judge", JUDGE_SYS)
    judge_sys = resolve_prompt(lf, "ic-data-bot-judge", JUDGE_SYS)
    system_blocks = build_system_blocks(snap)
    mistral_models = None
    if "--mistral-models" in argv:
        mistral_models = argv[argv.index("--mistral-models") + 1].split(",")
    agents = build_agents(cfg, system_blocks, toolbox, mistral_models)
    if not agents:
        print("Aucune clé provider — rien à lancer."); return 1

    grade = "--grade" in argv
    judge, judge_label = make_judge(cfg, judge_sys) if grade else (None, None)

    redact_on = cfg.langfuse_redact
    run_ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    # Un Dataset Run (= expérience Langfuse) nommé PAR MODÈLE → comparable run/run
    # dans l'onglet Experiments, sans fouiller les traces pour savoir qui a répondu.
    run_names = {name: f"{name}-{run_ts}" for name in agents}

    print(f"Providers : {', '.join(agents)}  |  Kestra : {kestra_log.count()} évts"
          + (f"  |  Juge : {judge_label}" if judge else "")
          + (f"  |  Langfuse exp : {', '.join(run_names.values())}" if lf else "") + "\n")

    # Sélection des questions
    if "-q" in argv:
        items = [{"id": "ad-hoc", "category": "?", "criteria": [],
                  "question": argv[argv.index("-q") + 1]}]
    else:
        catalog = load_questions()
        wanted = {a.upper() for a in argv if not a.startswith("-")}
        items = [q for q in catalog if q["id"].upper() in wanted] or catalog

    # scores[name][category] = [(met, total), ...]
    scores: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))

    # ── Mode Langfuse : un Dataset Run (= expérience) PAR MODÈLE, comparable dans
    #    l'onglet Experiments (API run_experiment du SDK v4 ; l'ancien item.run
    #    n'existe pas en 4.7). Le juge sert d'evaluator → score 'eval' par item. ──
    if lf:
        from langfuse.experiment import Evaluation
        sync_langfuse_dataset(lf, items)
        try:
            ds = lf.get_dataset(LANGFUSE_DATASET)
        except Exception as exc:
            print(f"[langfuse] get_dataset KO : {type(exc).__name__}: {exc}")
            return 1
        wanted = {f"icbot-{q['id']}" for q in items if q.get("criteria")}
        data = [it for it in ds.items if it.id in wanted]
        if not data:
            print("[langfuse] aucun item de dataset à évaluer (questions sans critères ?)")
            return 1

        # Versions de prompt → métadonnées du run (lien version↔expérience).
        def _pver(pname, fb):
            try:
                return lf.get_prompt(pname, label="production", fallback=fb,
                                     cache_ttl_seconds=300).version
            except Exception:
                return None
        persona_v = _pver("ic-data-bot-persona", "")
        judge_v = _pver("ic-data-bot-judge", JUDGE_SYS)

        def make_evaluator(model_name):
            def _eval(*, input, output, expected_output=None, metadata=None, **kw):
                if not judge:
                    return []
                crit = [c for c in (expected_output or "").split("\n") if c.strip()]
                question = input.get("question") if isinstance(input, dict) else str(input)
                cat = (metadata or {}).get("category", "?")
                try:
                    met, total, note = judge(question, crit, output)
                except Exception as exc:
                    met, total, note = 0, len(crit), f"juge KO {type(exc).__name__}"
                scores[model_name][cat].append((met, total))
                return Evaluation(name="eval", value=round(met / total, 3) if total else 0.0,
                                  comment=note[:300])
            return _eval

        for name, agent in agents.items():
            def task(*, item, _agent=agent, **kw):
                q = item.input.get("question") if isinstance(item.input, dict) else str(item.input)
                # On rédige la sortie AVANT envoi à Langfuse Cloud ; le juge évalue donc
                # le texte rédacté (IP/host internes masqués → ops peut sous-coter, artefact).
                return redact(_agent.answer(q, history=[]).text, redact_on)
            print(f"\n>>> Expérience {run_names[name]} ({len(data)} questions)…")
            try:
                res = lf.run_experiment(
                    name="ic-data-bot eval",
                    run_name=run_names[name],
                    description=f"éval {name} — {run_ts}",
                    data=data,
                    task=task,
                    evaluators=[make_evaluator(name)] if judge else [],
                    max_concurrency=4,
                    metadata={"model": getattr(agent, "model", name), "provider": name,
                              "persona_version": persona_v, "judge_version": judge_v},
                )
                try:
                    print(res.format())
                except Exception:
                    pass
            except Exception as exc:
                print(f"[langfuse] run_experiment {name} KO : {type(exc).__name__}: {exc}")
        lf.flush()
        if judge and scores:
            _print_score_matrix(scores)
        print(f"\nLangfuse : expériences {', '.join(run_names.values())} → dataset "
              f"'{LANGFUSE_DATASET}' (onglet Experiments, comparables run/run ; "
              f"{'sorties rédactées' if redact_on else 'sorties brutes'}).")
        return 0

    # ── Mode console (sans Langfuse) ──
    for q in items:
        print("=" * 70)
        print(f"[{q['id']}] {q['question']}")
        for name, agent in agents.items():
            t0 = time.monotonic()
            try:
                r = agent.answer(q["question"], history=[])
                dur = time.monotonic() - t0
                head = f"\n--- {name}  (iters={r.iterations}, tokens={r.tokens}, {dur:.1f}s"
                tool_line = "   ⚙ outils: " + (" → ".join(r.tools) if r.tools else "AUCUN")
                if judge and q["criteria"]:
                    try:
                        met, total, note = judge(q["question"], q["criteria"], r.text)
                    except Exception as exc:
                        met, total, note = 0, len(q["criteria"]), f"juge KO: {type(exc).__name__}"
                    scores[name][q["category"]].append((met, total))
                    head += f", score {met}/{total}"
                    print(head + ") ---")
                    print(tool_line)
                    print(r.text[:500] if grade else r.text)
                    print(f"   ⮑ juge: {note}")
                else:
                    print(head + ") ---")
                    print(tool_line)
                    print(r.text)
            except Exception as exc:
                print(f"\n--- {name} : ERREUR {type(exc).__name__}: {exc} ---")
        print()

    if judge and scores:
        _print_score_matrix(scores)
    return 0


def _print_score_matrix(scores: dict) -> None:
    cats = sorted({c for m in scores.values() for c in m})
    print("=" * 70)
    print("SCORES (critères remplis / total, par catégorie)\n")
    width = max(len(n) for n in scores)
    print(" " * (width + 2) + "  ".join(f"{c[:9]:>9}" for c in cats) + "   GLOBAL")
    for name, bycat in scores.items():
        cells = []
        tot_met = tot_all = 0
        for c in cats:
            pairs = bycat.get(c, [])
            met = sum(m for m, _ in pairs); tot = sum(t for _, t in pairs)
            tot_met += met; tot_all += tot
            cells.append(f"{(met/tot*100):>7.0f}%" if tot else f"{'—':>8}")
        glob = f"{(tot_met/tot_all*100):.0f}% ({tot_met}/{tot_all})" if tot_all else "—"
        print(f"{name:<{width}}  " + "  ".join(cells) + f"   {glob}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
