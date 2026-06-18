from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .claude import ISSUE_TITLE_PROMPT, DataManagerAgent
from .config import load_config
from .context import (
    SYSTEM_PERSONA,
    build_system_blocks,
    format_contracts_message,
    set_persona_source,
)
from .corrections import CorrectionsStore
from .discord_format import format_for_discord
from .github_issues import close_issue, contains_internal_details, create_issue, issue_body
from .guardrails import DailyBudget, RateLimiter, is_allowed_channel
from .kestra_events import KestraEventLog
from .telemetry import make_langfuse, redact, register_prompt
from .tools import RateLimitedError, ToolBox, redact_secrets

RATE_LIMITED_MSG = "Tu as posé trop de questions d'un coup, réessaie dans un instant. 🙏"
NO_BUDGET_MSG = "Le budget quotidien du bot est atteint, réessaie demain. 💤"
ERROR_MSG = "J'ai rencontré un souci technique en interrogeant le modèle, réessaie dans un moment. ⚙️"
MODEL_BUSY_MSG = "Le service de modèle est très sollicité à l'instant, réessaie dans un moment. 🙏"

# Marqueurs posés par le caviardage (tools.redact_secrets / secret_guard).
_REDACTION_MARKERS = ("‹secret-rédacté›", "‹clé-privée-rédactée›")
REDACTION_NOTICE = (
    "\n\n> ⚠️ Des éléments sensibles ont été masqués (‹secret-rédacté›) pour raisons de "
    "sécurité — ce n'est pas une erreur de ma part."
)

SOURCE_WARNING = (
    "> ⚠️ **Réponse non sourcée** — à vérifier : des sources étaient disponibles mais "
    "ne sont pas citées ci-dessous.\n\n"
)
# Détecte une source dans une réponse : URL http(s) OU citation `repo/chemin:lignes`.
_SOURCE_RX = re.compile(r"https?://|[\w.-]+/[\w./-]+:\d+(?:-\d+)?")


def _has_source(text: str) -> bool:
    """Vrai si la réponse cite au moins une source (URL ou chemin:lignes)."""
    return bool(_SOURCE_RX.search(text or ""))


def with_redaction_notice(text: str) -> str:
    """Ajoute un avis (une seule fois) si la réponse contient un marqueur de caviardage,
    pour que l'utilisateur ne prenne pas le masquage pour un bug de réponse."""
    if any(m in text for m in _REDACTION_MARKERS) and "pour raisons de sécurité" not in text:
        return text + REDACTION_NOTICE
    return text


def should_respond(*, author_is_bot: bool, channel_id: int, allowed_channel_id: int,
                   mentioned: bool, parent_channel_id: int | None = None,
                   is_bot_thread: bool = False) -> bool:
    """Répondre si : canal autorisé (ou fil dont le parent est le canal autorisé)
    ET (mention du bot OU fil ouvert par le bot — pas besoin de re-mentionner)."""
    if author_is_bot:
        return False
    in_scope = is_allowed_channel(channel_id, allowed_channel_id) or (
        parent_channel_id is not None
        and is_allowed_channel(parent_channel_id, allowed_channel_id)
    )
    if not in_scope:
        return False
    return mentioned or is_bot_thread


def split_for_discord(text: str, limit: int = 1900, max_messages: int = 3) -> list[str]:
    """Découpe une réponse en messages Discord (<= limit chacun), sur des
    frontières propres (paragraphe > ligne > espace), en refermant/rouvrant
    les blocs ``` à cheval sur deux messages. Remplace la troncature brute.

    Applique d'abord `format_for_discord` (tableaux Markdown → bloc ```, suppression des
    `---`) : fix DÉTERMINISTE du format, car les modèles (surtout Magistral en `!deep`)
    ignorent les consignes de la persona. Couvre tous les chemins (rapide, deep, incident)."""
    text = format_for_discord(text).strip()
    if not text:
        return [text]
    # Marge pour les fences ``` ajoutées en fermeture/réouverture.
    eff = limit - 8
    chunks: list[str] = []
    while text and len(chunks) < max_messages:
        if len(text) <= eff:
            chunks.append(text)
            text = ""
            break
        window = text[:eff]
        cut = window.rfind("\n\n")
        if cut < eff // 3:
            cut = window.rfind("\n")
        if cut < eff // 3:
            cut = window.rfind(" ")
        if cut < eff // 3:
            cut = eff
        chunk, text = text[:cut].rstrip(), text[cut:].lstrip()
        # Bloc de code ouvert mais pas refermé dans ce morceau → on referme,
        # et on rouvre dans le suivant.
        if chunk.count("```") % 2 == 1:
            chunk += "\n```"
            text = "```\n" + text
        chunks.append(chunk)
    if text:  # au-delà de max_messages : on tronque explicitement
        chunks[-1] = chunks[-1][: limit - 30].rstrip() + "\n*[réponse écourtée]*"
    return chunks


PROVIDER_LABELS = {"anthropic": "Claude", "mistral": "Mistral"}


def provider_nick(base: str, provider: str) -> str:
    """Pseudo du bot reflétant le provider actif (≤ 32 car., limite Discord)."""
    label = PROVIDER_LABELS.get(provider, provider)
    return f"{base} · {label}"[:32]


def format_budget_message(budget) -> str:
    """Réponse à `!budget` — état du plafond quotidien, sans appel au modèle."""
    remaining = budget.remaining()
    used = budget.limit - remaining
    pct = round(100 * used / budget.limit) if budget.limit else 0
    bar = "█" * min(10, round(pct / 10)) + "░" * max(0, 10 - round(pct / 10))
    fmt = lambda n: f"{n:,}".replace(",", " ")  # noqa: E731
    return (
        f"📊 **Budget tokens du jour** (reset à minuit)\n"
        f"`{bar}` {pct}% utilisé\n"
        f"• Utilisé : {fmt(used)} / {fmt(budget.limit)}\n"
        f"• Restant : {fmt(remaining)}"
    )


# Expliqueur d'incident : garde-fous anti-tempête
INCIDENT_COOLDOWN_S = 3600      # pas deux analyses du même flow en moins d'une heure
INCIDENT_MIN_SPACING_S = 120    # espacement global entre deux analyses

_FLOW_AFTER_FAILED = re.compile(r"Flow Failed\s*[—–-]*\s*([A-Za-z0-9_.-]+)")
_FLOW_GENERIC = re.compile(r"\b[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){2,}\b")


def extract_flow_id(text: str) -> str | None:
    """Id du flow dans une notification Kestra (« Flow Failed — ns.flow … »)."""
    t = text or ""
    m = _FLOW_AFTER_FAILED.search(t)
    if m and "." in m.group(1):
        return m.group(1).strip(".")
    m = _FLOW_GENERIC.search(t)
    return m.group(0) if m else None


def discord_message_text(message) -> str:
    """Contenu textuel d'un message Discord, embeds inclus (les notifications
    Kestra arrivent avec un content vide et tout dans l'embed)."""
    parts = [message.content or ""]
    for e in getattr(message, "embeds", []):
        for v in (getattr(e, "title", None), getattr(e, "description", None)):
            if v:
                parts.append(str(v))
        for f in getattr(e, "fields", None) or []:
            parts.append(f"{f.name}: {f.value}")
    return " | ".join(p for p in parts if p)


def install_ops_overlay(snapshot_dir, ops_path) -> bool:
    """Copie le mapping ops interne (volume persistant, jamais dans le repo
    public) dans le snapshot sous _ops/ : les outils read_file/grep/lineage
    le voient comme n'importe quel registre."""
    src = Path(ops_path)
    if not src.is_file():
        return False
    dst = Path(snapshot_dir) / "_ops" / "ops-mapping.yaml"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return True


def refresh_once(agent, snapshot_dir, *, sync_fn, corrections=None, ops_path=None) -> bool:
    """Un cycle de refresh : pull (sync_fn), réinstallation de l'overlay ops,
    puis reconstruction du noyau. Ne lève jamais ; False si échec (noyau inchangé)."""
    try:
        sync_fn()
        if ops_path:
            install_ops_overlay(snapshot_dir, ops_path)
        agent.system_blocks = build_system_blocks(Path(snapshot_dir), corrections)
        return True
    except Exception as exc:
        print(f"[refresh] échec : {type(exc).__name__}: {exc}", flush=True)
        return False


class ThreadHistory:
    """~N derniers tours par thread, avec TTL."""

    def __init__(self, max_turns: int, ttl_seconds: int, clock: Callable[[], float] = time.monotonic):
        self.max_turns = max_turns
        self.ttl = ttl_seconds
        self.clock = clock
        self._store: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self._last: dict[str, float] = {}

    def get(self, thread_id: str) -> list[dict]:
        if thread_id in self._last and self.clock() - self._last[thread_id] > self.ttl:
            self._store.pop(thread_id, None)
            self._last.pop(thread_id, None)
        msgs: list[dict] = []
        for q, a in self._store.get(thread_id, []):
            msgs.append({"role": "user", "content": q})
            msgs.append({"role": "assistant", "content": a})
        return msgs

    def append(self, thread_id: str, question: str, answer: str) -> None:
        turns = self._store[thread_id]
        turns.append((question, answer))
        del turns[: max(0, len(turns) - self.max_turns)]
        self._last[thread_id] = self.clock()


class BotApp:
    def __init__(self, agent: DataManagerAgent, rate_limiter: RateLimiter,
                 budget: DailyBudget, history: ThreadHistory, langfuse=None, redact=True):
        self.agent = agent
        self.rate_limiter = rate_limiter
        self.budget = budget
        self.history = history
        self.langfuse = langfuse
        self.redact = redact

    @staticmethod
    def _log(**fields) -> None:
        """Une ligne JSON par question sur stdout (docker logs) — observabilité."""
        record = {"evt": "question", "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        record.update(fields)
        print(json.dumps(record, ensure_ascii=False), flush=True)

    async def process(self, *, user_id: str, thread_id: str, question: str, agent=None) -> str:
        agent = agent or self.agent
        t0 = time.monotonic()
        if not self.rate_limiter.allow(user_id):
            self._log(user=user_id, status="rate_limited", q_chars=len(question))
            return RATE_LIMITED_MSG
        if not self.budget.has_budget():
            self._log(user=user_id, status="no_budget", q_chars=len(question))
            return NO_BUDGET_MSG
        history = self.history.get(thread_id)
        model = getattr(agent, "model", "?")
        trace = nullcontext(None)
        if self.langfuse is not None:
            trace = self.langfuse.start_as_current_observation(
                name="question", as_type="generation", model=model,
                input=redact(question, self.redact),
                metadata={"user": user_id, "thread": thread_id})
        try:
            with trace as gen:
                result = await asyncio.to_thread(agent.answer, question, history)
                if gen is not None:
                    gen.update(output=redact(result.text, self.redact),
                               usage_details={"total": result.tokens},
                               metadata={"iterations": result.iterations})
        except RateLimitedError as exc:  # 429 fournisseur persistant → invite à réessayer
            self._log(user=user_id, status="model_busy", q_chars=len(question),
                      model=model,
                      dur_s=round(time.monotonic() - t0, 2), error="RateLimited")
            return MODEL_BUSY_MSG
        except Exception as exc:  # ne jamais crasher le bot sur une erreur d'API/outil
            self._log(user=user_id, status="error", q_chars=len(question),
                      model=model,
                      dur_s=round(time.monotonic() - t0, 2), error=type(exc).__name__)
            return ERROR_MSG
        # Garde-fou de sortie : ultime passe déterministe avant que la réponse ne parte
        # vers Discord (et soit mémorisée). Filet au cas où un secret aurait transité malgré
        # le caviardage côté outils. redact_secrets est idempotent.
        scrubbed = redact_secrets(result.text)
        had_secret = any(m in scrubbed for m in _REDACTION_MARKERS)
        # Garde de sourçage : si des outils ont fourni des sources (URLs) mais que la réponse
        # n'en cite aucune, on signale visiblement (le modèle ignore parfois la persona).
        unsourced = bool(result.tools) and not _has_source(scrubbed)
        if unsourced:
            scrubbed = SOURCE_WARNING + scrubbed
            self._log(user=user_id, status="unsourced", q_chars=len(question),
                      model=model, dur_s=round(time.monotonic() - t0, 2))
        self.budget.add(result.tokens)
        self.history.append(thread_id, question, scrubbed)
        self._log(user=user_id, status="ok", q_chars=len(question),
                  model=getattr(agent, "model", "?"),
                  dur_s=round(time.monotonic() - t0, 2), tokens=result.tokens,
                  iters=result.iterations, reply_chars=len(scrubbed),
                  redacted=had_secret, budget_left=self.budget.remaining())
        return with_redaction_notice(scrubbed)


def run() -> None:  # pragma: no cover (point d'entrée I/O)
    import asyncio as _asyncio
    import discord
    from aiohttp import web
    from dotenv import load_dotenv

    load_dotenv()
    import os
    cfg = load_config(os.environ)

    from .health import ReadyState, make_health_app
    from . import gitsync

    snapshot = Path(cfg.snapshot_dir)
    install_ops_overlay(snapshot, cfg.ops_mapping_path)
    corrections = CorrectionsStore(Path(cfg.corrections_path))
    langfuse = make_langfuse(cfg)
    # Prompt management : versionne le persona (si changé) puis le sert depuis
    # Langfuse (fallback code) — build_system_blocks le résout via resolve_persona().
    register_prompt(langfuse, "ic-data-bot-persona", SYSTEM_PERSONA)
    set_persona_source(langfuse)
    system_blocks = build_system_blocks(snapshot, corrections)
    kestra_log = KestraEventLog()
    kestra_channels = {
        cid: label
        for cid, label in [
            (cfg.kestra_events_channel_id, "system-events"),
            (cfg.kestra_alerts_channel_id, "alerts"),
        ]
        if cid
    }
    meteofrance = None
    if cfg.meteofrance_application_id:
        from .meteofrance_api import MeteoFranceAuth
        meteofrance = MeteoFranceAuth(cfg.meteofrance_application_id)
    toolbox = ToolBox(snapshot, max_bytes=cfg.tool_read_max_bytes,
                      kestra_log=kestra_log if kestra_channels else None,
                      meteofrance=meteofrance)

    if cfg.provider == "mistral":
        from mistralai.client import Mistral
        from .mistral import MistralAgent
        client = Mistral(api_key=cfg.mistral_api_key)
        agent = MistralAgent(client, cfg.model, cfg.max_tokens, system_blocks, toolbox)
        # Agent de raisonnement (Magistral) — ciblé : !deep et expliqueur
        # d'incident. Même client/outils/préfixe ; modèle distinct.
        reasoning_agent = MistralAgent(client, cfg.mistral_reasoning_model,
                                       cfg.max_tokens, system_blocks, toolbox)
    else:
        import anthropic
        client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
        agent = DataManagerAgent(client, cfg.model, cfg.max_tokens, system_blocks, toolbox)
        reasoning_agent = agent  # pas de modèle de raisonnement distinct côté Anthropic

    # Guardrail modèle anti-fuite de secrets sur la sortie de search_code (le code legacy
    # indexé contient des secrets en dur). Couche sémantique derrière le caviardage regex.
    from .secret_guard import make_llm_scrubber
    toolbox.secret_scrub = make_llm_scrubber(
        cfg.provider, anthropic_key=cfg.anthropic_api_key, mistral_key=cfg.mistral_api_key)
    print(f"[secret-guard] scrub LLM {'actif' if toolbox.secret_scrub else 'désactivé'}",
          flush=True)
    reasoning_label = getattr(reasoning_agent, "model", "?")
    print(f"[provider] {cfg.provider} — modèle {cfg.model} "
          f"(raisonnement : {reasoning_label})", flush=True)

    app = BotApp(
        agent=agent,
        rate_limiter=RateLimiter(cfg.user_rate_limit, cfg.rate_window_seconds),
        budget=DailyBudget(Path(cfg.budget_state_path), cfg.daily_token_budget),
        history=ThreadHistory(cfg.history_max_turns, cfg.history_ttl_seconds),
        langfuse=langfuse,
        redact=cfg.langfuse_redact,
    )

    state = ReadyState(core_loaded=True)

    def _sync():
        gitsync.sync(cfg.clone_dir, cfg.repo_url, cfg.gitlab_deploy_user,
                     cfg.gitlab_deploy_token, cfg.branch)

    intents = discord.Intents.default()
    intents.message_content = True
    discord_client = discord.Client(intents=intents)

    async def _health_server():
        runner = web.AppRunner(make_health_app(state))
        await runner.setup()
        site = web.TCPSite(runner, cfg.health_bind, cfg.health_port)
        await site.start()
        return runner

    def _sync_reasoning():
        # L'agent de raisonnement partage le préfixe system : le re-synchroniser
        # après chaque reconstruction (refresh).
        if reasoning_agent is not agent:
            reasoning_agent.system_blocks = agent.system_blocks

    async def _refresh_loop():
        while True:
            await _asyncio.sleep(cfg.refresh_interval_seconds)
            await _asyncio.to_thread(refresh_once, agent, cfg.snapshot_dir,
                                     sync_fn=_sync, corrections=corrections,
                                     ops_path=cfg.ops_mapping_path)
            _sync_reasoning()

    async def _backfill_kestra():
        total = 0
        for cid, label in kestra_channels.items():
            try:
                channel = discord_client.get_channel(cid) or await discord_client.fetch_channel(cid)
                async for m in channel.history(limit=200):
                    kestra_log.record(m.created_at, label, discord_message_text(m))
                    total += 1
            except Exception as exc:
                print(f"[kestra] backfill #{label} impossible : {type(exc).__name__}", flush=True)
        print(f"[kestra] backfill terminé : {kestra_log.count()} événements en cache "
              f"({total} messages lus)", flush=True)

    incident_last_flow: dict[str, float] = {}
    incident_last_any = [float("-inf")]

    async def _explain_incident(message, flow: str) -> None:
        question = (
            f"INCIDENT : le flow Kestra `{flow}` vient d'échouer (FAILED). "
            "En t'appuyant sur les registres (outil lineage sur ce flow et ses "
            "outputs, kestra_recent pour voir s'il y a des échecs récurrents) : "
            "Si le flow EXACT est absent des registres, rattache-le à sa NAMESPACE / "
            "famille (ex. `infoclimat.backups`) via lineage sur le préfixe, et dis "
            "qu'il n'est pas encore inventorié — SANS inventer son rôle ni sa source. "
            "1) ce que fait ce flow (script, fréquence), "
            "2) l'impact aval CONCRET si ça ne repasse pas — tables non alimentées, "
            "cartes/services/notifications touchés, en termes métier, "
            "3) gravité (récurrence ? flow douteux/mort connu ?) et pièges documentés. "
            "Termine par l'action recommandée en une ligne. Bref et actionnable.\n"
            "FORMAT IMPÉRATIF (Discord) : JAMAIS de tableau Markdown (les `|` "
            "s'affichent en brut). Utilise des listes à puces, du **gras** pour les "
            "libellés et du `code` pour les noms techniques. Max 1500 caractères."
        )
        try:
            # Analyse d'impact = raisonnement profond → agent Magistral.
            reply = await app.process(user_id="system:incident",
                                      thread_id=f"incident:{message.id}",
                                      question=question, agent=reasoning_agent)
            chunks = split_for_discord(reply)
            try:
                target = await message.create_thread(
                    name=f"🔎 {flow}"[:95], auto_archive_duration=1440)
            except (discord.Forbidden, discord.HTTPException):
                target = None
            if target is None:
                await message.reply(chunks[0])
                for c in chunks[1:]:
                    await message.channel.send(c)
            else:
                for c in chunks:
                    await target.send(c)
        except Exception as exc:
            print(f"[incident] analyse impossible pour {flow} : {type(exc).__name__}", flush=True)

    async def _apply_identity():
        """Reflète le provider actif dans l'identité du bot : pseudo par serveur
        (gratuit) + avatar (rate-limité → seulement si le provider a changé)."""
        nick = provider_nick(discord_client.user.name, cfg.provider)
        for g in discord_client.guilds:
            try:
                await g.me.edit(nick=nick)
            except Exception as exc:
                print(f"[identity] pseudo KO ({g.id}) : {type(exc).__name__}", flush=True)
        # Avatar : logo assets/logo-<provider>.png si présent. L'édition d'avatar
        # est globale et fortement rate-limitée → on n'édite que si le provider a
        # changé depuis la dernière fois (marqueur dans le volume d'état).
        logo = Path("assets") / f"logo-{cfg.provider}.png"
        marker = Path(cfg.budget_state_path).parent / "avatar-provider.marker"
        if not logo.is_file():
            print(f"[identity] pas de logo {logo} (avatar inchangé)", flush=True)
            return
        prev = marker.read_text().strip() if marker.is_file() else ""
        if prev == cfg.provider:
            return
        try:
            await discord_client.user.edit(avatar=logo.read_bytes())
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(cfg.provider)
            print(f"[identity] avatar → {cfg.provider}", flush=True)
        except Exception as exc:
            print(f"[identity] avatar KO : {type(exc).__name__}", flush=True)

    @discord_client.event
    async def setup_hook():
        # Conserver des références fortes : sinon le AppRunner (et la tâche de
        # refresh) peuvent être collectés, et l'arrêt propre devient impossible.
        discord_client._health_runner = await _health_server()
        discord_client._refresh_task = _asyncio.create_task(_refresh_loop())
        if kestra_channels:
            discord_client._kestra_backfill = _asyncio.create_task(_backfill_kestra())

    @discord_client.event
    async def on_ready():
        state.discord_ready = True
        if not getattr(discord_client, "_identity_done", False):
            discord_client._identity_done = True
            await _apply_identity()

    @discord_client.event
    async def on_message(message: "discord.Message") -> None:
        ch = message.channel
        # Notifications Kestra (auteur bot, contenu en embed) : capturées pour
        # l'outil kestra_recent, avant tout filtre.
        if ch.id in kestra_channels:
            text = discord_message_text(message)
            kestra_log.record(message.created_at, kestra_channels[ch.id], text)
            # Expliqueur d'incident : sur échec franc dans #alerts (pas WARNING),
            # analyse d'impact postée en fil sous l'alerte. Garde-fous : cooldown
            # par flow, espacement global, budget quotidien.
            if (
                cfg.incident_explainer
                and ch.id == cfg.kestra_alerts_channel_id
                and "FAILED" in text
            ):
                flow = extract_flow_id(text)
                now = time.monotonic()
                if (
                    flow
                    and now - incident_last_flow.get(flow, float("-inf")) >= INCIDENT_COOLDOWN_S
                    and now - incident_last_any[0] >= INCIDENT_MIN_SPACING_S
                ):
                    if not app.budget.has_budget():
                        print(f"[incident] budget épuisé, analyse sautée : {flow}", flush=True)
                    else:
                        incident_last_flow[flow] = now
                        incident_last_any[0] = now
                        await _explain_incident(message, flow)
            return
        in_main = ch.id == cfg.allowed_channel_id
        in_thread = isinstance(ch, discord.Thread) and ch.parent_id == cfg.allowed_channel_id
        # Fil ouvert par le bot : on y répond sans exiger une nouvelle mention.
        in_bot_thread = in_thread and ch.owner_id == discord_client.user.id

        # Commandes utilitaires, sans appel au modèle (coût zéro) — valides dans
        # le canal ET dans ses fils (!fix s'utilise surtout en réponse, dans un fil).
        raw = message.content.strip()
        cmd_word = raw.split()[0].lower() if raw.startswith("!") else ""
        if (
            cmd_word
            and not message.author.bot
            and (in_main or in_thread)
        ):
            if cmd_word == "!version":
                sha = os.getenv("GIT_SHA", "dev")
                await message.reply(
                    f"🤖 ic-data-bot `{sha}` — modèle `{cfg.model}`, "
                    f"max_tokens {cfg.max_tokens}\n"
                    f"<https://github.com/AssociationInfoclimat/data-platform-bot/commit/{sha}>"
                )
                return
            if cmd_word == "!budget":
                await message.reply(format_budget_message(app.budget))
                return
            if cmd_word == "!contrats":
                await message.reply(format_contracts_message(Path(cfg.snapshot_dir))[:1900])
                return
            if cmd_word == "!refresh":  # git pull + reconstruction (bloquant → thread)
                async with message.channel.typing():
                    ok = await _asyncio.to_thread(
                        refresh_once, agent, cfg.snapshot_dir,
                        sync_fn=_sync, corrections=corrections,
                        ops_path=cfg.ops_mapping_path,
                    )
                    _sync_reasoning()
                await message.reply(
                    "🔄 Snapshot rafraîchi et index reconstruit."
                    if ok
                    else "⚠️ Échec du refresh (git pull ou reconstruction) — snapshot inchangé."
                )
                return
            if cmd_word == "!fix":
                text = raw[len("!fix"):].strip()
                if not text:
                    await message.reply(
                        "Usage : `!fix <correction>` — idéalement en **réponse** au message du bot à corriger."
                    )
                    return
                ref = None
                resolved = message.reference.resolved if message.reference else None
                if resolved is not None and getattr(resolved, "author", None) == discord_client.user:
                    ref = (resolved.content or "").strip()[:150]
                author = str(message.author.display_name)
                n_next = len(corrections.items()) + 1
                issue_no, issue_note = None, ""
                if cfg.github_token:
                    # data-platform est PUBLIC : pas d'issue si la correction
                    # (ou l'extrait cité) contient des détails d'infra interne.
                    if contains_internal_details(text) or contains_internal_details(ref or ""):
                        issue_note = ("\n⚠️ Détails internes détectés → pas d'issue publique. "
                                      "À reporter manuellement si besoin (en version expurgée).")
                    else:
                        issue_title = text[:80]
                        try:
                            r = await _asyncio.to_thread(
                                agent.thread_title, text, ISSUE_TITLE_PROMPT)
                            app.budget.add(r.tokens)
                            if 0 < len(r.text) <= 90:
                                issue_title = r.text
                        except Exception:
                            pass
                        try:
                            issue_no, issue_url = await _asyncio.to_thread(
                                create_issue, cfg.github_issues_repo, cfg.github_token,
                                f"[errata bot] {issue_title}",
                                issue_body(author, text, ref, n_next),
                            )
                            issue_note = f"\n🐙 Issue ouverte : <{issue_url}>"
                        except Exception:
                            issue_note = "\n⚠️ Création de l'issue GitHub échouée (erratum enregistré quand même)."
                n = corrections.add(author, text, ref, issue=issue_no)
                agent.system_blocks = build_system_blocks(Path(cfg.snapshot_dir), corrections)
                _sync_reasoning()
                await message.reply(
                    f"✅ Erratum **#{n}** enregistré — pris en compte dès la prochaine question."
                    f"{issue_note}\n"
                    f"_Après correction dans data-platform : `!refresh` puis `!unfix {n}`._"
                )
                return
            if cmd_word == "!fixes":
                items = corrections.items()
                if not items:
                    await message.reply("Aucun erratum actif. `!fix <correction>` pour en signaler un.")
                    return
                lines = [f"🩹 **Errata actifs ({len(items)})** — prioritaires sur le snapshot"]
                for i, it in enumerate(items, 1):
                    issue = f" · issue #{it['issue']}" if it.get("issue") else ""
                    lines.append(f"{i}. [{it['ts'][:10]}, {it['author']}] {it['text'][:150]}{issue}")
                lines.append("_Retrait après correction dans data-platform : `!unfix <n>`._")
                await message.reply("\n".join(lines)[:1900])
                return
            if cmd_word == "!unfix":
                arg = raw[len("!unfix"):].strip()
                removed = corrections.remove(int(arg)) if arg.isdigit() else None
                if removed is not None:
                    agent.system_blocks = build_system_blocks(Path(cfg.snapshot_dir), corrections)
                    _sync_reasoning()
                    note = ""
                    if removed.get("issue") and cfg.github_token:
                        try:
                            await _asyncio.to_thread(
                                close_issue, cfg.github_issues_repo, cfg.github_token,
                                removed["issue"], "Corrigé — erratum retiré du bot via `!unfix`.",
                            )
                            note = f" Issue #{removed['issue']} fermée."
                        except Exception:
                            note = f" (Échec de fermeture de l'issue #{removed['issue']} — à fermer à la main.)"
                    await message.reply(f"🗑️ Erratum #{arg} retiré.{note}")
                else:
                    await message.reply("Usage : `!unfix <n>` — numéro affiché par `!fixes`.")
                return
            # autre message commençant par "!" : pas une commande connue, on laisse filer

        me = message.guild.me if message.guild else None
        # Un @mention du bot résout souvent vers son rôle d'intégration géré
        # (<@&role_id>, même nom que le bot) plutôt que vers l'utilisateur-bot.
        # On accepte les deux.
        bot_role_ids = {
            r.id
            for r in (me.roles if me else [])
            if r.managed and r.tags and r.tags.bot_id == discord_client.user.id
        }
        mentioned = discord_client.user in message.mentions or any(
            r.id in bot_role_ids for r in message.role_mentions
        )
        if not should_respond(
            author_is_bot=message.author.bot,
            channel_id=ch.id,
            allowed_channel_id=cfg.allowed_channel_id,
            mentioned=mentioned,
            parent_channel_id=ch.parent_id if in_thread else None,
            is_bot_thread=in_bot_thread,
        ):
            return
        content = message.content
        for token in (f"<@{discord_client.user.id}>", f"<@!{discord_client.user.id}>"):
            content = content.replace(token, "")
        for rid in bot_role_ids:
            content = content.replace(f"<@&{rid}>", "")
        question = content.strip()

        # `!deep <question>` → route vers l'agent de raisonnement (Magistral),
        # plus lent/cher mais meilleur sur l'impact/lineage complexe. Sinon agent rapide.
        use_agent = agent
        if question.lower().startswith("!deep"):
            question = question[len("!deep"):].strip()
            use_agent = reasoning_agent
            if not question:
                await message.reply(
                    f"Usage : `!deep <question>` — analyse approfondie via "
                    f"`{reasoning_label}` (raisonnement). Pour les questions simples, "
                    "mentionne-moi normalement."
                )
                return

        # Question dans le canal principal → ouvrir un fil dédié : l'historique
        # de conversation est isolé par fil (sinon tous les devs partagent le
        # même contexte de 5 tours). Fallback canal si permission manquante.
        target = ch
        if in_main:
            # Titre du fil : résumé par le modèle (coût ~négligeable), sinon la
            # question tronquée si l'appel échoue.
            title = " ".join(question.split())[:80] or "Question data"
            try:
                r = await _asyncio.to_thread(agent.thread_title, question)
                app.budget.add(r.tokens)
                if 0 < len(r.text) <= 95:
                    title = r.text
            except Exception:
                pass
            try:
                target = await message.create_thread(name=title, auto_archive_duration=1440)
            except (discord.Forbidden, discord.HTTPException):
                target = ch  # permission « Créer des fils publics » absente

        thread_id = str(target.id)
        async with target.typing():
            reply = await app.process(user_id=str(message.author.id), thread_id=thread_id,
                                      question=question, agent=use_agent)
        chunks = split_for_discord(reply)
        if target is ch:
            await message.reply(chunks[0])
        else:
            await target.send(chunks[0])
        for chunk in chunks[1:]:
            await target.send(chunk)

    discord_client.run(cfg.discord_bot_token)
