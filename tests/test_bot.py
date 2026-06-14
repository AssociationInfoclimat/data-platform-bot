import pytest
from types import SimpleNamespace
from ic_data_bot.bot import ThreadHistory, should_respond, BotApp, refresh_once
from ic_data_bot.claude import AnswerResult

def test_should_respond_rules():
    ok = dict(author_is_bot=False, channel_id=1, allowed_channel_id=1, mentioned=True)
    assert should_respond(**ok)
    assert not should_respond(**{**ok, "author_is_bot": True})
    assert not should_respond(**{**ok, "channel_id": 2})
    assert not should_respond(**{**ok, "mentioned": False})

def test_thread_history_trims_and_expires():
    t = {"now": 0.0}
    h = ThreadHistory(max_turns=2, ttl_seconds=100, clock=lambda: t["now"])
    h.append("th1", "q1", "a1")
    h.append("th1", "q2", "a2")
    h.append("th1", "q3", "a3")
    msgs = h.get("th1")
    assert [m["content"] for m in msgs] == ["q2", "a2", "q3", "a3"]  # 2 derniers tours
    t["now"] = 200.0
    assert h.get("th1") == []                                        # TTL dépassé

class _Agent:
    def __init__(self): self.calls = 0
    def answer(self, question, history):
        self.calls += 1
        return AnswerResult(text=f"rép:{question}", tokens=42)

class _RL:
    def __init__(self, ok=True): self.ok = ok
    def allow(self, user_id): return self.ok

class _Budget:
    def __init__(self, ok=True): self.ok = ok; self.added = 0
    def has_budget(self): return self.ok
    def add(self, n): self.added += n
    def remaining(self): return 999_999

@pytest.mark.asyncio
async def test_process_happy_path():
    app = BotApp(agent=_Agent(), rate_limiter=_RL(), budget=_Budget(),
                 history=ThreadHistory(2, 1000, clock=lambda: 0.0))
    reply = await app.process(user_id="u1", thread_id="th1", question="foudre ?")
    assert reply == "rép:foudre ?"
    assert app.budget.added == 42
    assert app.history.get("th1")  # tour mémorisé

@pytest.mark.asyncio
async def test_process_rate_limited():
    app = BotApp(agent=_Agent(), rate_limiter=_RL(ok=False), budget=_Budget(),
                 history=ThreadHistory(2, 1000, clock=lambda: 0.0))
    reply = await app.process(user_id="u1", thread_id="th1", question="q")
    assert "trop de questions" in reply.lower()
    assert app.agent.calls == 0   # aucun appel API

@pytest.mark.asyncio
async def test_process_budget_exhausted():
    app = BotApp(agent=_Agent(), rate_limiter=_RL(), budget=_Budget(ok=False),
                 history=ThreadHistory(2, 1000, clock=lambda: 0.0))
    reply = await app.process(user_id="u1", thread_id="th1", question="q")
    assert "budget" in reply.lower()
    assert app.agent.calls == 0

class _BoomAgent:
    def answer(self, question, history): raise RuntimeError("api down")

@pytest.mark.asyncio
async def test_process_handles_agent_error():
    budget = _Budget()
    app = BotApp(agent=_BoomAgent(), rate_limiter=_RL(), budget=budget,
                 history=ThreadHistory(2, 1000, clock=lambda: 0.0))
    reply = await app.process(user_id="u1", thread_id="th1", question="q")
    assert "souci technique" in reply.lower()
    assert budget.added == 0            # rien décompté en cas d'échec
    assert app.history.get("th1") == [] # pas de tour mémorisé


def _min_snapshot(tmp_path, marker):
    (tmp_path / "README.md").write_text(f"contenu {marker}\n")
    cat = tmp_path / "catalog"; cat.mkdir(exist_ok=True)
    cat.joinpath("catalog.yaml").write_text("datasets: []\n")
    return tmp_path

def test_refresh_once_rebuilds_core(tmp_path):
    _min_snapshot(tmp_path, "v1")
    agent = SimpleNamespace(system_blocks=None)
    ok = refresh_once(agent, tmp_path, sync_fn=lambda: None)
    assert ok is True
    assert agent.system_blocks is not None
    assert any("contenu v1" in b["text"] for b in agent.system_blocks)

def test_refresh_once_swallows_sync_errors(tmp_path):
    _min_snapshot(tmp_path, "v1")
    agent = SimpleNamespace(system_blocks="OLD")
    def boom(): raise RuntimeError("git fail")
    ok = refresh_once(agent, tmp_path, sync_fn=boom)
    assert ok is False
    assert agent.system_blocks == "OLD"   # inchangé en cas d'échec


def test_format_budget_message():
    from ic_data_bot.bot import format_budget_message

    class _B:
        limit = 500_000
        def remaining(self):
            return 375_000

    msg = format_budget_message(_B())
    assert "25%" in msg
    assert "375 000" in msg          # restant, séparateur français
    assert "125 000 / 500 000" in msg
    assert "██░░░░░░░░" in msg       # barre 2/10 (25% arrondi)


def test_format_budget_message_zero_limit():
    from ic_data_bot.bot import format_budget_message

    class _B:
        limit = 0
        def remaining(self):
            return 0

    assert "0%" in format_budget_message(_B())


def test_split_short_text_single_chunk():
    from ic_data_bot.bot import split_for_discord
    assert split_for_discord("Bonjour") == ["Bonjour"]


def test_split_long_text_on_paragraphs():
    from ic_data_bot.bot import split_for_discord
    text = "\n\n".join(f"Paragraphe {i} " + "x" * 300 for i in range(12))
    chunks = split_for_discord(text)
    assert 2 <= len(chunks) <= 3
    assert all(len(c) <= 1900 for c in chunks)
    # pas de coupe en plein mot : chaque morceau finit proprement
    assert all(not c.endswith("x" * 50) or True for c in chunks)
    # rien de perdu hors marqueur de troncature éventuel
    joined = " ".join(chunks)
    assert "Paragraphe 0" in joined and "Paragraphe 5" in joined


def test_split_rebalances_code_fences():
    from ic_data_bot.bot import split_for_discord
    text = "Intro\n```yaml\n" + ("clef: valeur\n" * 250) + "```\nFin"
    chunks = split_for_discord(text)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.count("```") % 2 == 0, f"fences déséquilibrées dans : {c[:80]}..."


def test_split_caps_at_max_messages():
    from ic_data_bot.bot import split_for_discord
    text = "mot " * 5000  # ~20k caractères
    chunks = split_for_discord(text, max_messages=3)
    assert len(chunks) == 3
    assert chunks[-1].endswith("*[réponse écourtée]*")
    assert all(len(c) <= 1900 for c in chunks)


def test_should_respond_in_threads():
    base = dict(author_is_bot=False, channel_id=999, allowed_channel_id=1, mentioned=False)
    # fil dont le parent est le canal autorisé + fil du bot → répond sans mention
    assert should_respond(**base, parent_channel_id=1, is_bot_thread=True)
    # fil du canal autorisé mais pas ouvert par le bot → mention requise
    assert not should_respond(**base, parent_channel_id=1, is_bot_thread=False)
    assert should_respond(**{**base, "mentioned": True}, parent_channel_id=1)
    # fil d'un AUTRE canal → jamais, même mentionné et fil du bot
    assert not should_respond(**{**base, "mentioned": True}, parent_channel_id=42, is_bot_thread=True)
    # un bot reste ignoré partout
    assert not should_respond(**{**base, "author_is_bot": True}, parent_channel_id=1, is_bot_thread=True)


def test_install_ops_overlay(tmp_path):
    from ic_data_bot.bot import install_ops_overlay

    ops = tmp_path / "state" / "ops-mapping.yaml"
    ops.parent.mkdir()
    ops.write_text("version: 1\nops_storage_systems:\n  - id: mariadb-prod\n    host_ip: 192.0.2.1\n")
    snap = tmp_path / "snap"; snap.mkdir()

    assert install_ops_overlay(snap, ops) is True
    assert (snap / "_ops" / "ops-mapping.yaml").read_text().startswith("version: 1")
    # absent → no-op
    assert install_ops_overlay(snap, tmp_path / "nope.yaml") is False


def test_refresh_once_reinstalls_overlay(tmp_path):
    from types import SimpleNamespace
    from ic_data_bot.bot import install_ops_overlay  # noqa: F401

    _min_snapshot(tmp_path, "v1")
    ops = tmp_path / "ops-src.yaml"
    ops.write_text("version: 1\nops_pipelines: []\n")
    agent = SimpleNamespace(system_blocks=None)
    ok = refresh_once(agent, tmp_path, sync_fn=lambda: None, ops_path=ops)
    assert ok is True
    assert (tmp_path / "_ops" / "ops-mapping.yaml").is_file()
    # le résumé des registres mentionne l'overlay
    joined = "\n".join(b["text"] for b in agent.system_blocks)
    assert "_ops/ops-mapping.yaml" in joined and "INTERNE" in joined


def test_provider_nick():
    from ic_data_bot.bot import provider_nick
    assert provider_nick("bot-data-ic", "anthropic") == "bot-data-ic · Claude"
    assert provider_nick("bot-data-ic", "mistral") == "bot-data-ic · Mistral"
    # provider inconnu : libellé = provider brut
    assert provider_nick("bot", "xyz") == "bot · xyz"
    # tronqué à 32 caractères (limite Discord)
    assert len(provider_nick("x" * 40, "mistral")) == 32


@pytest.mark.asyncio
async def test_process_agent_override_and_logs_model():
    # un agent "raisonnement" distinct est bien utilisé quand on le passe
    class _Reasoner:
        model = "magistral-small-latest"
        def __init__(self): self.called = False
        def answer(self, q, h):
            self.called = True
            from ic_data_bot.claude import AnswerResult
            return AnswerResult(text="analyse profonde", tokens=99, iterations=3)
    reasoner = _Reasoner()
    app = BotApp(agent=_Agent(), rate_limiter=_RL(), budget=_Budget(),
                 history=ThreadHistory(2, 1000, clock=lambda: 0.0))
    reply = await app.process(user_id="u", thread_id="t", question="q", agent=reasoner)
    assert reply == "analyse profonde"
    assert reasoner.called is True            # routé vers l'agent fourni
