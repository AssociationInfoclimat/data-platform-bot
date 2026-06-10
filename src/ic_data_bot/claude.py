from __future__ import annotations

from dataclasses import dataclass

from .tools import SCHEMAS, ToolError

MAX_TOOL_ITERATIONS = 6


@dataclass
class AnswerResult:
    text: str
    tokens: int
    iterations: int = 1


def _count(usage) -> int:
    # Le budget quotidien est un garde-fou de coût. Les lectures de cache coûtent
    # ~0,1× le prix d'entrée : les compter à plein tarif vide le budget ~10× trop
    # vite dès qu'un gros préfixe system est mis en cache. On les pondère à 1/10.
    return int(
        getattr(usage, "input_tokens", 0)
        + getattr(usage, "output_tokens", 0)
        + getattr(usage, "cache_creation_input_tokens", 0)
        + getattr(usage, "cache_read_input_tokens", 0) // 10
    )


class DataManagerAgent:
    def __init__(self, client, model: str, max_tokens: int, system_blocks: list[dict], toolbox):
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.system_blocks = system_blocks
        self.toolbox = toolbox

    def answer(self, question: str, history: list[dict]) -> AnswerResult:
        messages: list[dict] = list(history) + [{"role": "user", "content": question}]
        total = 0

        # `thinking` adaptatif et `output_config.effort` ne sont supportés que
        # par les modèles 4.6+ (Opus 4.6/4.7/4.8, Sonnet 4.6, Fable). Haiku 4.5
        # les rejette (400) — on ne les envoie donc pas pour ces modèles.
        extra: dict = {}
        if "haiku" not in self.model:
            extra["thinking"] = {"type": "adaptive"}
            extra["output_config"] = {"effort": "medium"}

        for iteration in range(1, MAX_TOOL_ITERATIONS + 1):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=self.system_blocks,
                tools=SCHEMAS,
                messages=messages,
                **extra,
            )
            total += _count(resp.usage)

            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
                return AnswerResult(text=text.strip() or "(réponse vide)", tokens=total,
                                    iterations=iteration)

            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                try:
                    out = self.toolbox.dispatch(block.name, block.input)
                    is_error = False
                except ToolError as exc:
                    out, is_error = str(exc), True
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": out,
                    "is_error": is_error,
                })
            messages.append({"role": "user", "content": results})

        return AnswerResult(
            text="Désolé, je n'ai pas réussi à aboutir (trop d'allers-retours d'outils).",
            tokens=total,
            iterations=MAX_TOOL_ITERATIONS,
        )
