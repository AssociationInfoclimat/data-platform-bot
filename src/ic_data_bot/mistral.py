from __future__ import annotations

import hashlib
import json
import re

from .claude import ISSUE_TITLE_PROMPT, MAX_TOOL_ITERATIONS, TITLE_PROMPT, AnswerResult, _tool_trace
from .tools import SCHEMAS, ToolError

# Les modèles de raisonnement (Magistral) peuvent émettre leur réflexion dans
# <think>…</think> au sein du contenu. On la retire avant tout affichage Discord.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _cache_key(system_text: str) -> str:
    """Clé de cache de prompt Mistral, stable tant que le préfixe system ne
    change pas (il évolue au refresh du snapshot → nouvelle clé, nouveau cache).
    Permet à Mistral de réutiliser le préfixe (~11k tokens) à tarif réduit entre
    requêtes rapprochées ET entre itérations de la boucle d'outils."""
    return "icbot-" + hashlib.sha256(system_text.encode("utf-8")).hexdigest()[:24]

__all__ = ["MistralAgent", "ISSUE_TITLE_PROMPT", "TITLE_PROMPT"]


def _count(usage) -> int:
    # Mistral n'a pas de cache de prompt : pas de pondération à appliquer.
    return int(getattr(usage, "prompt_tokens", 0) + getattr(usage, "completion_tokens", 0))


def _text_of(content) -> str:
    """Texte d'un message — string ou liste de chunks. Les chunks de raisonnement
    (ThinkChunk, attribut `thinking`) sont ignorés ; les balises <think> en clair
    sont retirées (filet de sécurité pour les modèles Magistral)."""
    if content is None:
        return ""
    if isinstance(content, str):
        raw = content
    else:
        parts = []
        for c in content:
            t = getattr(c, "text", None)
            if t is None and isinstance(c, dict):
                t = c.get("text")
            if t:
                parts.append(t)
        raw = "".join(parts)
    return _THINK_RE.sub("", raw).strip()


def _mistral_tools() -> list[dict]:
    """Convertit les SCHEMAS (format Anthropic) au format fonction OpenAI/Mistral."""
    return [
        {
            "type": "function",
            "function": {
                "name": s["name"],
                "description": s["description"],
                "parameters": s["input_schema"],
            },
        }
        for s in SCHEMAS
    ]


class MistralAgent:
    """Adaptateur Mistral, interface identique à DataManagerAgent (claude.py)
    pour être interchangeable derrière le switch PROVIDER."""

    def __init__(self, client, model: str, max_tokens: int, system_blocks: list[dict], toolbox):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.system_blocks = system_blocks  # forme Anthropic, aplatie à l'usage
        self.toolbox = toolbox
        self._tools = _mistral_tools()

    def _system_text(self) -> str:
        return "\n\n".join(b["text"] for b in self.system_blocks if b.get("text"))

    def thread_title(self, question: str, prompt: str = TITLE_PROMPT) -> AnswerResult:
        """Titre court (fil Discord, issue…) — appel minimal, sans outils."""
        resp = self.client.chat.complete(
            model=self.model,
            max_tokens=30,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": question[:500]},
            ],
            prompt_cache_key=_cache_key(prompt),
        )
        text = _text_of(resp.choices[0].message.content)
        title = " ".join(text.split()).strip(" \"'«»“”")
        return AnswerResult(text=title, tokens=_count(resp.usage), iterations=1)

    def answer(self, question: str, history: list[dict]) -> AnswerResult:
        system_text = self._system_text()
        cache_key = _cache_key(system_text)
        messages: list[dict] = [{"role": "system", "content": system_text}]
        messages += list(history)
        messages.append({"role": "user", "content": question})
        total = 0
        tools_used: list[str] = []

        for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
            # tool_choice="any" force un appel d'outil à la 1re itération : Mistral
            # suit mal les ordres du préfixe (« lis la source ») et confabulait des
            # détails (unités Kelvin/Pa) en répondant AUCUN outil. On le contraint
            # donc à s'ancrer (read_file/grep/lineage) AVANT toute réponse, au niveau
            # API. Itérations suivantes en "auto" (sinon il rebouclerait sans pouvoir
            # produire la réponse finale).
            resp = self.client.chat.complete(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=messages,
                tools=self._tools,
                tool_choice="any" if iteration == 1 else "auto",
                prompt_cache_key=cache_key,
            )
            total += _count(resp.usage)
            choice = resp.choices[0]
            msg = choice.message

            if choice.finish_reason != "tool_calls" or not msg.tool_calls:
                text = _text_of(msg.content)
                return AnswerResult(text=text.strip() or "(réponse vide)", tokens=total,
                                    iterations=iteration, tools=tools_used)

            # Ré-émettre le message assistant (contenu + appels d'outils).
            messages.append({
                "role": "assistant",
                "content": _text_of(msg.content),
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                    tools_used.append(_tool_trace(tc.function.name, args))
                    out = self.toolbox.dispatch(tc.function.name, args)
                except ToolError as exc:
                    out = str(exc)
                except json.JSONDecodeError:
                    tools_used.append(f"{tc.function.name}(<json invalide>)")
                    out = "Arguments d'outil JSON invalides."
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": out,
                })

        return AnswerResult(
            text="Désolé, je n'ai pas réussi à aboutir (trop d'allers-retours d'outils).",
            tokens=total,
            iterations=MAX_TOOL_ITERATIONS,
        )
