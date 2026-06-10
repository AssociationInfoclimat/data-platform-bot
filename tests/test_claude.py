from types import SimpleNamespace
from ic_data_bot.claude import DataManagerAgent, AnswerResult

class _Box:
    def __init__(self): self.calls = []
    def dispatch(self, name, tool_input):
        self.calls.append((name, tool_input))
        return "contenu: id: foudre"

def _usage(i, o):
    return SimpleNamespace(input_tokens=i, output_tokens=o,
                           cache_creation_input_tokens=0, cache_read_input_tokens=0)

def _text(t): return SimpleNamespace(type="text", text=t)
def _tool(id_, name, inp): return SimpleNamespace(type="tool_use", id=id_, name=name, input=inp)

class _FakeMessages:
    def __init__(self, responses): self._responses = list(responses); self.kwargs = []
    def create(self, **kw):
        self.kwargs.append(kw)
        return self._responses.pop(0)

class _FakeClient:
    def __init__(self, responses): self.messages = _FakeMessages(responses)

def test_direct_answer_no_tools():
    client = _FakeClient([
        SimpleNamespace(stop_reason="end_turn", content=[_text("Réponse directe.")], usage=_usage(10, 5)),
    ])
    agent = DataManagerAgent(client, "claude-sonnet-4-6", 3000, [{"type": "text", "text": "sys"}], _Box())
    res = agent.answer("question", history=[])
    assert isinstance(res, AnswerResult)
    assert res.text == "Réponse directe."
    assert res.tokens == 15

def test_tool_loop_then_answer():
    box = _Box()
    client = _FakeClient([
        SimpleNamespace(stop_reason="tool_use",
                        content=[_tool("tu_1", "read_file", {"path": "contracts/foudre.odcs.yaml"})],
                        usage=_usage(20, 10)),
        SimpleNamespace(stop_reason="end_turn",
                        content=[_text("Le contrat foudre couvre V5.foudre.")],
                        usage=_usage(30, 8)),
    ])
    agent = DataManagerAgent(client, "claude-sonnet-4-6", 3000, [{"type": "text", "text": "sys"}], box)
    res = agent.answer("contrat foudre ?", history=[])
    assert box.calls == [("read_file", {"path": "contracts/foudre.odcs.yaml"})]
    assert "foudre" in res.text
    assert res.tokens == 68
    # second appel : le tool_result a bien été renvoyé
    second = client.messages.kwargs[1]["messages"]
    assert any(
        isinstance(m["content"], list) and m["content"][0].get("type") == "tool_result"
        for m in second if m["role"] == "user"
    )

def test_tool_error_is_reported(monkeypatch):
    from ic_data_bot.tools import ToolError
    class _BadBox:
        def dispatch(self, name, tool_input): raise ToolError("Fichier introuvable : x")
    client = _FakeClient([
        SimpleNamespace(stop_reason="tool_use",
                        content=[_tool("tu_1", "read_file", {"path": "x"})], usage=_usage(5, 5)),
        SimpleNamespace(stop_reason="end_turn", content=[_text("désolé")], usage=_usage(5, 5)),
    ])
    agent = DataManagerAgent(client, "claude-sonnet-4-6", 3000, [{"type": "text", "text": "sys"}], _BadBox())
    agent.answer("q", history=[])
    results_turn = client.messages.kwargs[1]["messages"][-1]
    assert results_turn["content"][0]["is_error"] is True
