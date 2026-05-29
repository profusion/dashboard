from pathlib import Path
from unittest.mock import patch

from kernelCI_app.management.commands.helpers.commit_enrichment import (
    enrich_commit_checkout,
    enrich_commit_checkouts,
)


@patch(
    "kernelCI_app.management.commands.helpers.commit_enrichment.COMMIT_ENRICHMENT_ENABLED",
    False,
)
def test_enrich_commit_checkouts_disabled():
    result = enrich_commit_checkouts(
        [
            {
                "id": "checkout",
                "git_repository_url": "https://example.com/repo.git",
                "git_commit_hash": "abc123",
            }
        ]
    )

    assert result == {}


@patch(
    "kernelCI_app.management.commands.helpers.commit_enrichment.enrich_commit_checkout"
)
def test_enrich_commit_checkouts_skips_missing_identity(mock_enrich):
    result = enrich_commit_checkouts(
        [
            {"id": "missing-url", "git_commit_hash": "abc123"},
            {
                "id": "missing-hash",
                "git_repository_url": "https://example.com/repo.git",
            },
        ]
    )

    assert result == {}
    mock_enrich.assert_not_called()


@patch(
    "kernelCI_app.management.commands.helpers.commit_enrichment.enrich_commit_checkout"
)
def test_enrich_commit_checkouts_catches_failures(mock_enrich):
    mock_enrich.side_effect = RuntimeError("git failed")

    result = enrich_commit_checkouts(
        [
            {
                "id": "checkout",
                "git_repository_url": "https://example.com/repo.git",
                "git_commit_hash": "abc123",
            }
        ]
    )

    assert result == {}


@patch(
    "kernelCI_app.management.commands.helpers.commit_enrichment._tags_pointing_at_commit"
)
@patch(
    "kernelCI_app.management.commands.helpers.commit_enrichment._ls_remote_branch_tip"
)
@patch("kernelCI_app.management.commands.helpers.commit_enrichment._run_git")
@patch("kernelCI_app.management.commands.helpers.commit_enrichment._ensure_repo_cache")
def test_enrich_commit_checkout_with_mocked_git(
    mock_ensure_repo_cache,
    mock_run_git,
    mock_ls_remote_branch_tip,
    mock_tags_pointing_at_commit,
):
    mock_ensure_repo_cache.return_value = Path("/tmp/repo")
    mock_run_git.side_effect = [
        "",
        "commit subject",
        "commit subject\n\ncommit body",
        "2026-05-29T12:00:00+00:00",
    ]
    mock_ls_remote_branch_tip.return_value = "abc123"
    mock_tags_pointing_at_commit.return_value = ["v6.1"]

    result = enrich_commit_checkout(
        {
            "id": "checkout",
            "git_repository_url": "https://example.com/repo.git",
            "git_repository_branch": "main",
            "git_commit_hash": "abc123",
        }
    )

    assert result["git_commit_name"] == "commit subject"
    assert result["git_commit_message"] == "commit subject\n\ncommit body"
    assert result["git_repository_branch_tip"] is True
    assert result["git_commit_tags"] == ["v6.1"]
    assert result["commit_time"].isoformat() == "2026-05-29T12:00:00+00:00"
