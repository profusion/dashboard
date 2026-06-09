from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime


def _parse_datetime_option(value: str | None, option_name: str):
    if value is None:
        return None

    parsed = parse_datetime(value)
    if parsed is None:
        raise CommandError(f"Invalid {option_name}: {value}")

    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


class Command(BaseCommand):
    help = (
        "Backfill commits from existing checkout columns and link checkouts.commit_id. "
        "This command is data-only and never fetches git repositories."
    )

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=5000)
        parser.add_argument("--resume-from-id", type=int, default=0)
        parser.add_argument("--since")
        parser.add_argument("--until")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size <= 0:
            raise CommandError("--batch-size must be greater than zero")

        since = _parse_datetime_option(options["since"], "--since")
        until = _parse_datetime_option(options["until"], "--until")
        last_id = options["resume_from_id"]
        dry_run = options["dry_run"]

        total_selected = 0
        total_updated = 0

        while True:
            checkout_ids = self._next_checkout_ids(
                batch_size=batch_size,
                last_id=last_id,
                since=since,
                until=until,
            )
            if not checkout_ids:
                break

            total_selected += len(checkout_ids)
            last_id = checkout_ids[-1]

            if dry_run:
                self.stdout.write(
                    f"Would process {len(checkout_ids)} checkouts through id={last_id}"
                )
                continue

            with transaction.atomic():
                self._upsert_commits(checkout_ids)
                updated = self._link_checkouts(checkout_ids)
                total_updated += updated

            self.stdout.write(
                f"Processed {len(checkout_ids)} checkouts through id={last_id}; "
                f"linked {updated}"
            )

        action = "Would process" if dry_run else "Processed"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {total_selected} checkouts; linked {total_updated}"
            )
        )

    def _next_checkout_ids(
        self,
        *,
        batch_size: int,
        last_id: int,
        since,
        until,
    ) -> list[int]:
        clauses = ["git_commit_hash IS NOT NULL", "id > %s"]
        params = [last_id]

        if since is not None:
            clauses.append("start_time >= %s")
            params.append(since)
        if until is not None:
            clauses.append("start_time <= %s")
            params.append(until)

        params.append(batch_size)

        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id
                FROM checkouts
                WHERE {" AND ".join(clauses)}
                ORDER BY id
                LIMIT %s
                """,
                params,
            )
            return [row[0] for row in cursor.fetchall()]

    def _upsert_commits(self, checkout_ids: list[int]) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH selected_checkouts AS (
                    SELECT *
                    FROM checkouts
                    WHERE id = ANY(%s)
                        AND git_commit_hash IS NOT NULL
                ),
                latest_commit_contexts AS (
                    SELECT DISTINCT ON (
                        tree_name,
                        git_repository_url,
                        git_repository_branch,
                        git_commit_hash
                    )
                        _timestamp,
                        tree_name,
                        git_repository_url,
                        git_commit_hash,
                        git_commit_name,
                        git_repository_branch,
                        git_commit_message,
                        git_repository_branch_tip,
                        git_commit_tags,
                        patchset_files,
                        patchset_hash,
                        message_id,
                        comment
                    FROM selected_checkouts
                    ORDER BY
                        tree_name NULLS FIRST,
                        git_repository_url NULLS FIRST,
                        git_repository_branch NULLS FIRST,
                        git_commit_hash,
                        git_commit_tags IS NULL,
                        git_commit_name IS NULL,
                        git_commit_message IS NULL,
                        git_repository_branch_tip IS NULL,
                        patchset_files IS NULL,
                        patchset_hash IS NULL,
                        message_id IS NULL,
                        comment IS NULL,
                        _timestamp DESC NULLS LAST
                )
                INSERT INTO commits (
                    _timestamp,
                    tree_name,
                    git_repository_url,
                    git_commit_hash,
                    git_commit_name,
                    git_repository_branch,
                    git_commit_message,
                    git_repository_branch_tip,
                    git_commit_tags,
                    patchset_files,
                    patchset_hash,
                    message_id,
                    comment
                )
                SELECT
                    _timestamp,
                    tree_name,
                    git_repository_url,
                    git_commit_hash,
                    git_commit_name,
                    git_repository_branch,
                    git_commit_message,
                    git_repository_branch_tip,
                    git_commit_tags,
                    patchset_files,
                    patchset_hash,
                    message_id,
                    comment
                FROM latest_commit_contexts
                ON CONFLICT (
                    tree_name,
                    git_repository_url,
                    git_repository_branch,
                    git_commit_hash
                )
                DO UPDATE SET
                    _timestamp = GREATEST(commits._timestamp, EXCLUDED._timestamp),
                    git_commit_name = COALESCE(
                        commits.git_commit_name,
                        EXCLUDED.git_commit_name
                    ),
                    git_commit_message = COALESCE(
                        commits.git_commit_message,
                        EXCLUDED.git_commit_message
                    ),
                    git_repository_branch_tip = COALESCE(
                        commits.git_repository_branch_tip,
                        EXCLUDED.git_repository_branch_tip
                    ),
                    git_commit_tags = COALESCE(
                        commits.git_commit_tags,
                        EXCLUDED.git_commit_tags
                    ),
                    patchset_files = COALESCE(
                        commits.patchset_files,
                        EXCLUDED.patchset_files
                    ),
                    patchset_hash = COALESCE(
                        commits.patchset_hash,
                        EXCLUDED.patchset_hash
                    ),
                    message_id = COALESCE(commits.message_id, EXCLUDED.message_id),
                    comment = COALESCE(commits.comment, EXCLUDED.comment)
                """,
                [checkout_ids],
            )

    def _link_checkouts(self, checkout_ids: list[int]) -> int:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE checkouts AS checkout
                SET commit_id = commits.id
                FROM commits
                WHERE checkout.id = ANY(%s)
                    AND checkout.commit_id IS NULL
                    AND checkout.git_commit_hash IS NOT NULL
                    AND commits.tree_name IS NOT DISTINCT FROM checkout.tree_name
                    AND commits.git_repository_url IS NOT DISTINCT FROM
                        checkout.git_repository_url
                    AND commits.git_repository_branch IS NOT DISTINCT FROM
                        checkout.git_repository_branch
                    AND commits.git_commit_hash = checkout.git_commit_hash
                """,
                [checkout_ids],
            )
            return cursor.rowcount
