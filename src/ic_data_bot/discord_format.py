"""Adapte une réponse Markdown au rendu limité de Discord (déterministe, à l'envoi).

Discord ne rend ni les tableaux Markdown ni les règles horizontales (`---`). Les petits
modèles — surtout l'agent de raisonnement Magistral en `!deep` — ignorent les consignes de
format de la persona. On corrige donc côté code, avant l'envoi :
- tableaux Markdown (`| … |` avec une ligne de séparation `|---|`) → enveloppés dans un bloc
  ``` (monospace : les `|` restent visibles et alignés, lisible, au lieu d'être cassés) ;
- séparateurs `---` / `***` / `___` seuls sur une ligne → supprimés (ligne vide).

Le contenu déjà dans un bloc ``` (code/YAML/SQL) n'est jamais modifié.
"""
from __future__ import annotations

import re

_SEP = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")


def _is_row(line: str) -> bool:
    return line.count("|") >= 2


def _is_sep_row(line: str) -> bool:
    """Ligne de séparation d'un tableau Markdown, ex. `|---|:--:|`."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return bool(cells) and all(c and set(c) <= set("-:") for c in cells) and any("-" in c for c in cells)


def format_for_discord(text: str) -> str:
    """Rend `text` lisible sur Discord (tableaux → bloc code, suppression des `---`)."""
    if not text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):       # entrée/sortie d'un bloc code → tel quel
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue
        if _SEP.match(line):                       # règle horizontale → ligne vide (dédupliquée)
            if out and out[-1] != "":
                out.append("")
            i += 1
            continue
        if _is_row(line):                          # début possible d'un tableau Markdown
            j = i
            block = []
            while j < len(lines) and _is_row(lines[j]) and not lines[j].lstrip().startswith("```"):
                block.append(lines[j])
                j += 1
            if any(_is_sep_row(b) for b in block):  # vrai tableau → enveloppé en monospace
                out.append("```")
                out.extend(block)
                out.append("```")
            else:                                   # simples `|` en prose → inchangé
                out.extend(block)
            i = j
            continue
        out.append(line)
        i += 1
    return "\n".join(out)
