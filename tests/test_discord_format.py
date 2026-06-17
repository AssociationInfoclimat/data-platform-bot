"""Tests du formatage Discord (code_index n/a — bot pur)."""
from __future__ import annotations

from ic_data_bot.discord_format import format_for_discord


def test_markdown_table_wrapped_in_code_block():
    src = "Voici :\n| A | B |\n|---|---|\n| 1 | 2 |\nfin"
    out = format_for_discord(src)
    assert "```" in out
    # le tableau est désormais dans un bloc ``` (donc lisible en monospace)
    assert out.count("```") == 2
    body = out.split("```")[1]
    assert "| A | B |" in body and "| 1 | 2 |" in body
    assert out.startswith("Voici :") and out.rstrip().endswith("fin")


def test_horizontal_rules_removed():
    out = format_for_discord("a\n---\nb\n***\nc\n___\nd")
    assert "---" not in out and "***" not in out and "___" not in out
    for c in ("a", "b", "c", "d"):
        assert c in out


def test_existing_code_block_untouched():
    src = "```sql\nSELECT 1;\n---\n| pas | un | tableau |\n```"
    out = format_for_discord(src)
    assert out == src  # rien dans un bloc ``` n'est modifié


def test_plain_text_and_bullets_untouched():
    src = "**Titre**\n- puce 1\n- puce 2 avec `code`\nphrase normale."
    assert format_for_discord(src) == src


def test_prose_with_pipes_not_wrapped():
    # des `|` sans ligne de séparation ne forment pas un tableau → inchangé
    src = "commande : cat a | grep b | wc -l"
    assert format_for_discord(src) == src


def test_empty():
    assert format_for_discord("") == ""
