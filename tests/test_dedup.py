from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.vault.dedup import find_similar


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    proj_dir = tmp_path / "40_Projects"
    proj_dir.mkdir(parents=True)
    (proj_dir / "legai.md").write_text(
        "---\ntype: project\naliases:\n- LegAI\n---\n\nLegAI project.\n", encoding="utf-8"
    )
    (proj_dir / "echelon.md").write_text(
        "---\ntype: project\naliases: []\n---\n\nEchelon project.\n", encoding="utf-8"
    )
    return tmp_path


@pytest.mark.asyncio
async def test_find_similar_exact_alias(vault: Path) -> None:
    with patch("src.vault.dedup.settings") as ms:
        ms.vault_path = str(vault)
        results = await find_similar("project", "LegAI")
    assert results and results[0].path.endswith("legai.md")
    assert results[0].score >= 90


@pytest.mark.asyncio
async def test_find_similar_no_match(vault: Path) -> None:
    with patch("src.vault.dedup.settings") as ms:
        ms.vault_path = str(vault)
        results = await find_similar("project", "новый-проект-xyz-123")
    assert results == []


@pytest.mark.asyncio
async def test_find_similar_wrong_type(vault: Path) -> None:
    """task folder doesn't exist — should return empty."""
    with patch("src.vault.dedup.settings") as ms:
        ms.vault_path = str(vault)
        results = await find_similar("task", "legai")
    assert results == []


# ── person-dedup regression: Лола (мама) ≠ Хилола (дочка) ────────────────────
#
# Production bug observed 2026-05-14: writing a `person` entity for "Лола"
# silently routed to an existing "Хилола" note (WRatio=90 via substring boost
# from partial_ratio). Mother's facts ended up on daughter's note, bidirectional
# links pointed at the wrong human, and the user had to call out the merge by
# hand. Fix: person dedup uses fuzz.ratio (no partial), HARD gate bumped to 92.
@pytest.fixture
def people_vault(tmp_path: Path) -> Path:
    people_dir = tmp_path / "20_People"
    people_dir.mkdir(parents=True)
    (people_dir / "хилола.md").write_text(
        "---\ntype: person\naliases: []\n---\n\nХилола.\n", encoding="utf-8"
    )
    (people_dir / "джамолиддин.md").write_text(
        "---\ntype: person\naliases: []\n---\n\nДжамолиддин.\n", encoding="utf-8"
    )
    return tmp_path


@pytest.mark.asyncio
async def test_person_short_name_substring_not_merged(people_vault: Path) -> None:
    """Substring containment (Лола ⊂ Хилола) must NOT cross the hard gate.

    Pure Levenshtein gives 80 → below the 92 person-hard threshold; the
    candidate may still surface as a soft warning (≥70) but write_pipeline
    won't auto-reroute.
    """
    from src.vault.write_pipeline import _hard_threshold

    with patch("src.vault.dedup.settings") as ms:
        ms.vault_path = str(people_vault)
        results = await find_similar("person", "Лола")

    # Soft surface allowed; hard reroute forbidden.
    assert results, "expected Хилола to surface as a soft candidate"
    assert results[0].score < _hard_threshold("person"), (
        f"Лола↔Хилола scored {results[0].score} ≥ {_hard_threshold('person')} — "
        f"would silently merge two distinct people"
    )


@pytest.mark.asyncio
async def test_person_long_vs_short_prefix_not_merged(people_vault: Path) -> None:
    """Джамол (short) must NOT auto-merge into existing Джамолиддин (long).

    WRatio would score 90 via partial_ratio=100; ratio gives 70.6 → safe.
    """
    from src.vault.write_pipeline import _hard_threshold

    with patch("src.vault.dedup.settings") as ms:
        ms.vault_path = str(people_vault)
        results = await find_similar("person", "Джамол")

    if results:
        assert results[0].score < _hard_threshold("person")


@pytest.mark.asyncio
async def test_person_exact_match_still_merges(people_vault: Path) -> None:
    """Sanity: identical name must still trip the hard gate (we didn't break
    legitimate dedup while fixing the false-positive)."""
    from src.vault.write_pipeline import _hard_threshold

    with patch("src.vault.dedup.settings") as ms:
        ms.vault_path = str(people_vault)
        results = await find_similar("person", "Хилола")

    assert results
    assert results[0].score >= _hard_threshold("person")


@pytest.mark.asyncio
async def test_non_person_keeps_wratio_aggressive_merge(tmp_path: Path) -> None:
    """Themes/jobs should still consolidate via WRatio — substring overlap is
    a feature there ("семейный ресторан" ↔ "ресторан-сан-жозе" are close
    enough to consider merging at human review)."""
    themes_dir = tmp_path / "80_Themes"
    themes_dir.mkdir(parents=True)
    (themes_dir / "бухгалтерия-компании.md").write_text(
        "---\ntype: theme\naliases: []\n---\n\n", encoding="utf-8"
    )

    with patch("src.vault.dedup.settings") as ms:
        ms.vault_path = str(tmp_path)
        results = await find_similar("theme", "бухгалтерия")

    assert results
    # WRatio gives high score on this kind of overlap — that's intended for
    # non-person types, where consolidation is a benign reduction.
    assert results[0].score >= 85
