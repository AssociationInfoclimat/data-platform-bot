from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .claude import DataManagerAgent
from .config import load_config
from .context import build_system_blocks, format_contracts_message
from .corrections import CorrectionsStore
from .guardrails import DailyBudget, RateLimiter, is_allowed_channel
from .tools import ToolBox

RATE_LIMITED_MSG = "Tu as posé trop de questions d'un coup, réessaie dans un instant. 🙏"
NO_BUDGET_MSG = "Le budget quotidien du bot est atteint, réessaie demain. 💤"
ERROR_MSG = "J'ai rencontré un souci technique en interrogeant le modèle, réessaie dans un moment. ⚙️"


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
    les blocs ``` à cheval sur deux messages. Remplace la troncature brute."""
    text = text.strip()
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
    except Exception:
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
                 budget: DailyBudget, history: ThreadHistory):
        self.agent = agent
        self.rate_limiter = rate_limiter
        self.budget = budget
        self.history = history

    @staticmethod
    def _log(**fields) -> None:
        """Une ligne JSON par question sur stdout (docker logs) — observabilité."""
        record = {"evt": "question", "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        record.update(fields)
        print(json.dumps(record, ensure_ascii=False), flush=True)

    async def process(self, *, user_id: str, thread_id: str, question: str) -> str:
        t0 = time.monotonic()
        if not self.rate_limiter.allow(user_id):
            self._log(user=user_id, status="rate_limited", q_chars=len(question))
            return RATE_LIMITED_MSG
        if not self.budget.has_budget():
            self._log(user=user_id, status="no_budget", q_chars=len(question))
            return NO_BUDGET_MSG
        history = self.history.get(thread_id)
        try:
            result = await asyncio.to_thread(self.agent.answer, question, history)
        except Exception as exc:  # ne jamais crasher le bot sur une erreur d'API/outil
            self._log(user=user_id, status="error", q_chars=len(question),
                      dur_s=round(time.monotonic() - t0, 2), error=type(exc).__name__)
            return ERROR_MSG
        self.budget.add(result.tokens)
        self.history.append(thread_id, question, result.text)
        self._log(user=user_id, status="ok", q_chars=len(question),
                  dur_s=round(time.monotonic() - t0, 2), tokens=result.tokens,
                  iters=result.iterations, reply_chars=len(result.text),
                  budget_left=self.budget.remaining())
        return result.text


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
    system_blocks = build_system_blocks(snapshot, corrections)
    toolbox = ToolBox(snapshot, max_bytes=cfg.tool_read_max_bytes)

    import anthropic
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)
    agent = DataManagerAgent(client, cfg.model, cfg.max_tokens, system_blocks, toolbox)

    app = BotApp(
        agent=agent,
        rate_limiter=RateLimiter(cfg.user_rate_limit, cfg.rate_window_seconds),
        budget=DailyBudget(Path(cfg.budget_state_path), cfg.daily_token_budget),
        history=ThreadHistory(cfg.history_max_turns, cfg.history_ttl_seconds),
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

    async def _refresh_loop():
        while True:
            await _asyncio.sleep(cfg.refresh_interval_seconds)
            await _asyncio.to_thread(refresh_once, agent, cfg.snapshot_dir,
                                     sync_fn=_sync, corrections=corrections,
                                     ops_path=cfg.ops_mapping_path)

    @discord_client.event
    async def setup_hook():
        # Conserver des références fortes : sinon le AppRunner (et la tâche de
        # refresh) peuvent être collectés, et l'arrêt propre devient impossible.
        discord_client._health_runner = await _health_server()
        discord_client._refresh_task = _asyncio.create_task(_refresh_loop())

    @discord_client.event
    async def on_ready():
        state.discord_ready = True

    @discord_client.event
    async def on_message(message: "discord.Message") -> None:
        ch = message.channel
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
                n = corrections.add(str(message.author.display_name), text, ref)
                agent.system_blocks = build_system_blocks(Path(cfg.snapshot_dir), corrections)
                await message.reply(
                    f"✅ Erratum **#{n}** enregistré — pris en compte dès la prochaine question.\n"
                    f"_Pense à corriger `data-platform/` en amont, puis `!unfix {n}`. Liste : `!fixes`._"
                )
                return
            if cmd_word == "!fixes":
                items = corrections.items()
                if not items:
                    await message.reply("Aucun erratum actif. `!fix <correction>` pour en signaler un.")
                    return
                lines = [f"🩹 **Errata actifs ({len(items)})** — prioritaires sur le snapshot"]
                for i, it in enumerate(items, 1):
                    lines.append(f"{i}. [{it['ts'][:10]}, {it['author']}] {it['text'][:150]}")
                lines.append("_Retrait après correction dans data-platform : `!unfix <n>`._")
                await message.reply("\n".join(lines)[:1900])
                return
            if cmd_word == "!unfix":
                arg = raw[len("!unfix"):].strip()
                if arg.isdigit() and corrections.remove(int(arg)):
                    agent.system_blocks = build_system_blocks(Path(cfg.snapshot_dir), corrections)
                    await message.reply(f"🗑️ Erratum #{arg} retiré.")
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
            reply = await app.process(user_id=str(message.author.id), thread_id=thread_id, question=question)
        chunks = split_for_discord(reply)
        if target is ch:
            await message.reply(chunks[0])
        else:
            await target.send(chunks[0])
        for chunk in chunks[1:]:
            await target.send(chunk)

    discord_client.run(cfg.discord_bot_token)
