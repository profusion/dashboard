import hashlib
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils.dateparse import parse_datetime

from utils.validation import is_boolean_or_string_true

logger = logging.getLogger(__name__)

GIT_TIMEOUT_SECONDS = int(os.environ.get("COMMIT_ENRICHMENT_GIT_TIMEOUT_SECONDS", "20"))
GIT_RETRIES = int(os.environ.get("COMMIT_ENRICHMENT_GIT_RETRIES", "1"))
GIT_CACHE_DIR = Path(
    os.environ.get(
        "COMMIT_ENRICHMENT_GIT_CACHE_DIR",
        os.path.join(settings.BACKEND_DATA_DIR, "git-cache"),
    )
)
COMMIT_ENRICHMENT_ENABLED = is_boolean_or_string_true(
    os.environ.get("COMMIT_ENRICHMENT_ENABLED", True)
)

CommitEnrichment = dict[str, Any]


def enrich_commit_checkouts(
    checkouts: list[dict[str, Any]],
) -> dict[str, CommitEnrichment]:
    if not COMMIT_ENRICHMENT_ENABLED:
        return {}

    enrichments: dict[str, CommitEnrichment] = {}
    for checkout in checkouts:
        checkout_id = checkout.get("id")
        git_url = checkout.get("git_repository_url")
        commit_hash = checkout.get("git_commit_hash")

        if not checkout_id or not git_url or not commit_hash:
            continue

        try:
            enrichment = enrich_commit_checkout(checkout)
        except Exception:
            logger.exception("Failed to enrich commit for checkout %s", checkout_id)
            continue

        if enrichment:
            enrichments[checkout_id] = enrichment

    return enrichments


def enrich_commit_checkout(checkout: dict[str, Any]) -> CommitEnrichment:
    git_url = checkout["git_repository_url"]
    commit_hash = checkout["git_commit_hash"]
    repo_dir = _ensure_repo_cache(git_url)

    _run_git(["git", "-C", str(repo_dir), "fetch", "--depth=1", "origin", commit_hash])

    enrichment: CommitEnrichment = {}
    enrichment["git_commit_name"] = _run_git(
        ["git", "-C", str(repo_dir), "show", "-s", "--format=%s", commit_hash]
    )
    enrichment["git_commit_message"] = _run_git(
        ["git", "-C", str(repo_dir), "show", "-s", "--format=%B", commit_hash]
    ).strip()

    commit_time_raw = _run_git(
        ["git", "-C", str(repo_dir), "show", "-s", "--format=%cI", commit_hash]
    )
    commit_time = parse_datetime(commit_time_raw)
    if commit_time is not None:
        enrichment["commit_time"] = commit_time

    branch = checkout.get("git_repository_branch")
    if branch:
        branch_tip = _ls_remote_branch_tip(git_url, branch)
        if branch_tip:
            enrichment["git_repository_branch_tip"] = branch_tip == commit_hash

    tags = _tags_pointing_at_commit(repo_dir, commit_hash)
    if tags:
        enrichment["git_commit_tags"] = tags

    return enrichment


def _ensure_repo_cache(git_url: str) -> Path:
    GIT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    repo_dir = GIT_CACHE_DIR / hashlib.sha256(git_url.encode("utf-8")).hexdigest()
    if repo_dir.exists():
        return repo_dir

    repo_dir.mkdir(parents=True)
    _run_git(
        [
            "git",
            "init",
            "--bare",
            str(repo_dir),
        ]
    )
    _run_git(["git", "-C", str(repo_dir), "remote", "add", "origin", git_url])
    return repo_dir


def _ls_remote_branch_tip(git_url: str, branch: str) -> str | None:
    output = _run_git(["git", "ls-remote", "--heads", git_url, branch])
    if not output:
        return None
    return output.split()[0]


def _tags_pointing_at_commit(repo_dir: Path, commit_hash: str) -> list[str]:
    output = _run_git(["git", "-C", str(repo_dir), "tag", "--points-at", commit_hash])
    return [tag for tag in output.splitlines() if tag]


def _run_git(command: list[str]) -> str:
    last_error: subprocess.CalledProcessError | subprocess.TimeoutExpired | None = None
    for _ in range(GIT_RETRIES + 1):
        try:
            result = subprocess.run(  # noqa: S603 - git args are built as fixed argv lists.
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            last_error = error

    assert last_error is not None
    raise last_error
