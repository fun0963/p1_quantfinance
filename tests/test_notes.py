"""Knowledge-base notes: create -> parse round-trip, collisions, listing."""
from __future__ import annotations

import pytest

from quant.research.notes import create_note, list_notes, parse_note


def test_create_and_parse_round_trip(tmp_path):
    p = create_note("Momentum beats MA-cross OOS", status="adopted",
                    strategy="momentum", symbols=("SPY",), experiments=(3, 4),
                    notes_dir=tmp_path)
    assert p.exists() and p.suffix == ".md"

    n = parse_note(p)
    assert n.title == "Momentum beats MA-cross OOS"
    assert n.status == "adopted" and n.strategy == "momentum"
    assert n.symbols == ["SPY"] and n.experiments == [3, 4]
    assert n.created and n.created == n.updated
    assert "## 假設" in n.body and "## 結論" in n.body      # template sections present


def test_same_day_title_collision_gets_suffix(tmp_path):
    a = create_note("same idea", notes_dir=tmp_path)
    b = create_note("same idea", notes_dir=tmp_path)
    assert a != b and a.exists() and b.exists()
    assert b.stem.endswith("-2")


def test_invalid_status_rejected(tmp_path):
    with pytest.raises(ValueError, match="status must be one of"):
        create_note("x", status="maybe", notes_dir=tmp_path)


def test_list_notes_filters_by_status_and_skips_readme(tmp_path):
    create_note("keeper", status="adopted", notes_dir=tmp_path)
    create_note("loser", status="rejected", notes_dir=tmp_path)
    (tmp_path / "README.md").write_text("not a note", encoding="utf-8")

    assert len(list_notes(tmp_path)) == 2                    # README skipped
    rejected = list_notes(tmp_path, status="rejected")
    assert [n.title for n in rejected] == ["loser"]


def test_missing_dir_is_empty_knowledge_base(tmp_path):
    assert list_notes(tmp_path / "absent") == []


def test_note_without_frontmatter_raises(tmp_path):
    p = tmp_path / "2026-01-01-bad.md"
    p.write_text("just prose", encoding="utf-8")
    with pytest.raises(ValueError, match="missing frontmatter"):
        parse_note(p)


def test_unicode_title_slug_is_filesystem_safe(tmp_path):
    p = create_note("動量 vs 均線交叉!", notes_dir=tmp_path)
    assert p.exists()
    assert parse_note(p).title == "動量 vs 均線交叉!"
