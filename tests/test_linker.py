from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.linker import LinkBatch, LinkProposal


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "40_Projects").mkdir(parents=True)
    (tmp_path / "50_Tasks").mkdir(parents=True)
    (tmp_path / "40_Projects" / "legai.md").write_text(
        "---\ntype: project\naliases:\n- LegAI\n---\n\nAI startup project.\n",
        encoding="utf-8",
    )
    (tmp_path / "50_Tasks" / "deploy.md").write_text(
        "---\ntype: task\naliases: []\n---\n\nDeploy to prod.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.asyncio
async def test_propose_links_returns_mock_result(vault: Path) -> None:
    mock_parsed = LinkBatch(links=[
        LinkProposal(
            from_path="50_Tasks/deploy.md",
            to_path="40_Projects/legai.md",
            relation="for_project",
        )
    ])
    mock_response = MagicMock()
    mock_response.choices[0].message.parsed = mock_parsed

    mock_client = MagicMock()
    mock_client.beta.chat.completions.parse = AsyncMock(return_value=mock_response)

    with (
        patch("src.agent.linker.get_client", return_value=mock_client),
        patch("src.agent.linker.settings") as ms,
    ):
        ms.vault_path = str(vault)
        ms.openai_model_main = "gpt-4o"

        from src.agent.linker import propose_links
        proposals = await propose_links(
            ["50_Tasks/deploy.md", "40_Projects/legai.md"],
            "_meta/owner.md",
        )

    assert len(proposals) == 1
    assert proposals[0].relation == "for_project"
    assert proposals[0].from_path == "50_Tasks/deploy.md"


@pytest.mark.asyncio
async def test_apply_links_calls_add_typed_link(vault: Path) -> None:
    links = [
        LinkProposal(
            from_path="50_Tasks/deploy.md",
            to_path="40_Projects/legai.md",
            relation="for_project",
        )
    ]

    # Test that apply_links delegates to add_typed_link
    with patch("src.vault.linking.add_typed_link", new_callable=AsyncMock) as mock_atl:
        mock_atl.return_value = True

        from src.agent.linker import apply_links
        count = await apply_links(links, "ses_test")

    mock_atl.assert_called_once_with(
        from_path="50_Tasks/deploy.md",
        to_path="40_Projects/legai.md",
        relation="for_project",
        session_id="ses_test",
    )
    assert count == 1
