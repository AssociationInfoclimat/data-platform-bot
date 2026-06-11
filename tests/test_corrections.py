from pathlib import Path

from ic_data_bot.corrections import CorrectionsStore
from ic_data_bot.context import build_system_blocks


def test_add_persist_reload_remove(tmp_path):
    p = tmp_path / "corrections.jsonl"
    store = CorrectionsStore(p)
    assert store.items() == []

    n1 = store.add("pam", "la colonne toto est de type B", ref_excerpt="toto est de type A")
    n2 = store.add("alice", "le job X est décommissionné")
    assert (n1, n2) == (1, 2)

    # persistance : un nouveau store relit le fichier
    store2 = CorrectionsStore(p)
    assert len(store2.items()) == 2
    assert store2.items()[0]["author"] == "pam"
    assert store2.items()[0]["ref"].startswith("toto est")

    removed = store2.remove(1)
    assert removed["author"] == "pam"          # l'item retiré est renvoyé
    assert len(CorrectionsStore(p).items()) == 1
    assert store2.remove(99) is None


def test_text_capped_and_max_items(tmp_path):
    store = CorrectionsStore(tmp_path / "c.jsonl", max_items=3)
    for i in range(5):
        store.add("u", f"correction {i} " + "x" * 600)
    items = store.items()
    assert len(items) == 3                      # plafond
    assert items[0]["text"].startswith("correction 2")  # les plus récents gardés
    assert all(len(it["text"]) <= 500 for it in items)  # texte borné


def test_render_block_and_placement_after_cache_breakpoint(tmp_path):
    # snapshot minimal
    (tmp_path / "README.md").write_text("doc\n")
    store = CorrectionsStore(tmp_path / "c.jsonl")

    # sans erratum : 2 blocs, cache_control sur le dernier
    blocks = build_system_blocks(tmp_path, store)
    assert len(blocks) == 2
    assert "cache_control" in blocks[1]

    # avec erratum : 3e bloc APRÈS le breakpoint, sans cache_control
    store.add("pam", "toto est de type B")
    blocks = build_system_blocks(tmp_path, store)
    assert len(blocks) == 3
    assert "cache_control" in blocks[1]
    assert "cache_control" not in blocks[2]
    assert "PRIORITAIRES" in blocks[2]["text"]
    assert "toto est de type B" in blocks[2]["text"]


def test_render_none_when_empty(tmp_path):
    store = CorrectionsStore(tmp_path / "c.jsonl")
    assert store.render_system_block() is None


def test_add_with_issue_number(tmp_path):
    store = CorrectionsStore(tmp_path / "c.jsonl")
    store.add("pam", "toto type B", issue=42)
    item = CorrectionsStore(tmp_path / "c.jsonl").items()[0]
    assert item["issue"] == 42
    assert store.remove(1)["issue"] == 42
