from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Validate that checkouts with commit hashes are linked to matching commits."

    def add_arguments(self, parser):
        parser.add_argument("--sample-size", type=int, default=20)
        parser.add_argument("--fail-on-mismatch", action="store_true")

    def handle(self, *args, **options):
        sample_size = options["sample_size"]
        if sample_size < 0:
            raise CommandError("--sample-size must be zero or greater")

        summary = self._fetch_summary()
        samples = self._fetch_mismatch_samples(sample_size)

        self.stdout.write(f"eligible_checkouts={summary['eligible_checkouts']}")
        self.stdout.write(f"linked_checkouts={summary['linked_checkouts']}")
        self.stdout.write(f"missing_links={summary['missing_links']}")
        self.stdout.write(f"mismatched_links={summary['mismatched_links']}")

        if samples:
            self.stdout.write("Mismatch samples:")
            for sample in samples:
                self.stdout.write(
                    "  checkout_id={checkout_id} commit_id={commit_id} "
                    "checkout_scope=({checkout_tree}, {checkout_url}, "
                    "{checkout_branch}, {checkout_hash}) commit_scope=({commit_tree}, "
                    "{commit_url}, {commit_branch}, {commit_hash})".format(**sample)
                )

        has_mismatch = summary["missing_links"] or summary["mismatched_links"]
        if has_mismatch and options["fail_on_mismatch"]:
            raise CommandError("Commit backfill validation failed")

        if has_mismatch:
            self.stdout.write(self.style.WARNING("Commit backfill validation failed"))
        else:
            self.stdout.write(self.style.SUCCESS("Commit backfill validation passed"))

    def _fetch_summary(self) -> dict[str, int]:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE checkout.git_commit_hash IS NOT NULL
                    ) AS eligible_checkouts,
                    COUNT(*) FILTER (
                        WHERE checkout.git_commit_hash IS NOT NULL
                            AND checkout.commit_id IS NOT NULL
                    ) AS linked_checkouts,
                    COUNT(*) FILTER (
                        WHERE checkout.git_commit_hash IS NOT NULL
                            AND checkout.commit_id IS NULL
                    ) AS missing_links,
                    COUNT(*) FILTER (
                        WHERE checkout.git_commit_hash IS NOT NULL
                            AND checkout.commit_id IS NOT NULL
                            AND NOT (
                                commits.tree_name IS NOT DISTINCT FROM
                                    checkout.tree_name
                                AND commits.git_repository_url IS NOT DISTINCT FROM
                                    checkout.git_repository_url
                                AND commits.git_repository_branch IS NOT DISTINCT FROM
                                    checkout.git_repository_branch
                                AND commits.git_commit_hash = checkout.git_commit_hash
                            )
                    ) AS mismatched_links
                FROM checkouts AS checkout
                LEFT JOIN commits ON commits.id = checkout.commit_id
                """
            )
            row = cursor.fetchone()

        return {
            "eligible_checkouts": row[0],
            "linked_checkouts": row[1],
            "missing_links": row[2],
            "mismatched_links": row[3],
        }

    def _fetch_mismatch_samples(self, sample_size: int) -> list[dict]:
        if sample_size == 0:
            return []

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    checkout.id AS checkout_id,
                    checkout.commit_id,
                    checkout.tree_name AS checkout_tree,
                    checkout.git_repository_url AS checkout_url,
                    checkout.git_repository_branch AS checkout_branch,
                    checkout.git_commit_hash AS checkout_hash,
                    commits.tree_name AS commit_tree,
                    commits.git_repository_url AS commit_url,
                    commits.git_repository_branch AS commit_branch,
                    commits.git_commit_hash AS commit_hash
                FROM checkouts AS checkout
                LEFT JOIN commits ON commits.id = checkout.commit_id
                WHERE checkout.git_commit_hash IS NOT NULL
                    AND (
                        checkout.commit_id IS NULL
                        OR NOT (
                            commits.tree_name IS NOT DISTINCT FROM checkout.tree_name
                            AND commits.git_repository_url IS NOT DISTINCT FROM
                                checkout.git_repository_url
                            AND commits.git_repository_branch IS NOT DISTINCT FROM
                                checkout.git_repository_branch
                            AND commits.git_commit_hash = checkout.git_commit_hash
                        )
                    )
                ORDER BY checkout.id
                LIMIT %s
                """,
                [sample_size],
            )
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
