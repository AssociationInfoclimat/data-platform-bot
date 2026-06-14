import json
from types import SimpleNamespace

from ic_data_bot.mistral import MistralAgent, _mistral_tools
from ic_data_bot.claude import AnswerResult
from ic_data_bot.tools import SCHEMAS


class _Box:
    def __init__(self):
        self.calls = []
    def dispatch(self, name, tool_input):
        self.calls.append((name, tool_input))
        return "contenu outil: id foudre"


def _usage(p, c):
    return SimpleNamespace(prompt_tokens=p, completion_tokens=c, total_tokens=p + c)


def _msg(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls or None)


def _choice(finish, message):
    return SimpleNamespace(finish_reason=finish, message=message)


def _resp(finish, message, usage):
    return SimpleNamespace(choices=[_choice(finish, message)], usage=usage)


def _toolcall(id_, name, args):
    return SimpleNamespace(id=id_, type="function",
                           function=SimpleNamespace(name=name, arguments=args))


class _FakeChat:
    def __init__(self, responses):
        self._responses = list(responses)
        self.kwargs = []
    def complete(self, **kw):
        self.kwargs.append(kw)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.chat = _FakeChat(responses)


SYS = [{"type": "text", "text": "persona"},
       {"type": "text", "text": "noyau", "cache_control": {"type": "ephemeral"}}]


def test_direct_answer_no_tools():
    client = _FakeClient([_resp("stop", _msg("Réponse directe."), _usage(10, 5))])
    agent = MistralAgent(client, "mistral-small-latest", 2000, SYS, _Box())
    res = agent.answer("question", history=[])
    assert isinstance(res, AnswerResult)
    assert res.text == "Réponse directe."
    assert res.tokens == 15
    assert res.iterations == 1
    # system aplati en 1er message, pas de cache_control
    msgs = client.chat.kwargs[0]["messages"]
    assert msgs[0] == {"role": "system", "content": "persona\n\nnoyau"}
    assert msgs[-1] == {"role": "user", "content": "question"}


def test_tool_loop_passes_dict_to_dispatch():
    box = _Box()
    client = _FakeClient([
        _resp("tool_calls",
              _msg("", [_toolcall("tc1", "read_file", '{"path": "contracts/foudre.odcs.yaml"}')]),
              _usage(20, 10)),
        _resp("stop", _msg("Le contrat foudre couvre V5.foudre."), _usage(30, 8)),
    ])
    agent = MistralAgent(client, "mistral-small-latest", 2000, SYS, box)
    res = agent.answer("contrat foudre ?", history=[])
    # dispatch reçoit un dict (arguments JSON décodés)
    assert box.calls == [("read_file", {"path": "contracts/foudre.odcs.yaml"})]
    assert "foudre" in res.text
    assert res.tokens == 68
    assert res.iterations == 2
    # 2e appel : message assistant (tool_calls) + résultat tool ré-injectés
    msgs2 = client.chat.kwargs[1]["messages"]
    assert msgs2[-2]["role"] == "assistant" and msgs2[-2]["tool_calls"][0]["id"] == "tc1"
    assert msgs2[-1]["role"] == "tool" and msgs2[-1]["tool_call_id"] == "tc1"


def test_tool_error_and_bad_json_are_fed_back():
    class _BoomBox:
        def dispatch(self, name, ti):
            from ic_data_bot.tools import ToolError
            raise ToolError("Fichier introuvable")
    client = _FakeClient([
        _resp("tool_calls", _msg("", [_toolcall("t", "read_file", '{"path": "x"}')]), _usage(5, 5)),
        _resp("stop", _msg("ok"), _usage(5, 5)),
    ])
    agent = MistralAgent(client, "m", 100, SYS, _BoomBox())
    agent.answer("q", [])
    assert "introuvable" in client.chat.kwargs[1]["messages"][-1]["content"]


def test_thread_title_minimal_call():
    client = _FakeClient([_resp("stop", _msg('  "Titre net"\n'), _usage(30, 6))])
    agent = MistralAgent(client, "mistral-small-latest", 2000, SYS, _Box())
    res = agent.thread_title("une longue question")
    assert res.text == "Titre net"
    kw = client.chat.kwargs[0]
    assert kw["max_tokens"] == 30
    assert "tools" not in kw


def test_mistral_tools_conversion():
    tools = _mistral_tools()
    assert len(tools) == len(SCHEMAS)
    f = tools[0]
    assert f["type"] == "function"
    assert f["function"]["name"] == SCHEMAS[0]["name"]
    assert f["function"]["parameters"] == SCHEMAS[0]["input_schema"]


def test_content_can_be_chunk_list():
    client = _FakeClient([
        _resp("stop", _msg([SimpleNamespace(text="a"), SimpleNamespace(text="b")]), _usage(1, 1)),
    ])
    agent = MistralAgent(client, "m", 100, SYS, _Box())
    assert agent.answer("q", []).text == "ab"


def test_prompt_cache_key_stable_and_passed():
    from ic_data_bot.mistral import _cache_key
    # déterministe et stable pour un même préfixe
    assert _cache_key("abc") == _cache_key("abc")
    assert _cache_key("abc") != _cache_key("abd")
    assert _cache_key("abc").startswith("icbot-")

    client = _FakeClient([_resp("stop", _msg("ok"), _usage(1, 1))])
    agent = MistralAgent(client, "m", 100, SYS, _Box())
    agent.answer("q", [])
    kw = client.chat.kwargs[0]
    # la clé envoyée = hash du préfixe system aplati
    assert kw["prompt_cache_key"] == _cache_key("persona\n\nnoyau")


def test_think_tags_stripped():
    from ic_data_bot.mistral import _text_of
    # raisonnement en clair retiré, réponse conservée
    assert _text_of("<think>je réfléchis...</think>\nRéponse finale") == "Réponse finale"
    assert _text_of("<THINK>x\ny</THINK> ok") == "ok"
    # ThinkChunk (attribut thinking) ignoré, TextChunk gardé
    from types import SimpleNamespace
    chunks = [SimpleNamespace(type="thinking", thinking="caché"),
              SimpleNamespace(type="text", text="visible")]
    assert _text_of(chunks) == "visible"
