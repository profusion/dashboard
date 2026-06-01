from typing import Literal

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

Phase = Literal["all", "builds", "tests", "incidents"]


class Command(BaseCommand):
    help = "Backfill build/test definition and run tables from legacy builds/tests."

    def add_arguments(self, parser):
        parser.add_argument(
            "--phase",
            choices=["all", "builds", "tests", "incidents"],
            default="all",
        )
        parser.add_argument("--batch-size", type=int, default=5000)
        parser.add_argument("--max-batches", type=int)
        parser.add_argument("--resume-from-id", default="")
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help="Only process legacy rows without matching run rows.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        if batch_size <= 0:
            raise CommandError("--batch-size must be greater than zero")

        phase: Phase = options["phase"]
        max_batches = options["max_batches"]
        if max_batches is not None and max_batches <= 0:
            raise CommandError("--max-batches must be greater than zero")
        resume_from_id = options["resume_from_id"]
        missing_only = options["missing_only"]
        dry_run = options["dry_run"]

        if phase in {"all", "builds"}:
            self._backfill_builds(
                batch_size, resume_from_id, dry_run, max_batches, missing_only
            )
        if phase in {"all", "tests"}:
            if phase == "all" and resume_from_id:
                resume_from_id = ""
            self._backfill_tests(
                batch_size, resume_from_id, dry_run, max_batches, missing_only
            )
        if phase in {"all", "incidents"}:
            self._backfill_incidents(dry_run)

    def _backfill_builds(
        self,
        batch_size: int,
        resume_from_id: str,
        dry_run: bool,
        max_batches: int | None,
        missing_only: bool,
    ) -> None:
        total = 0
        batches = 0
        last_id = resume_from_id
        while True:
            if max_batches is not None and batches >= max_batches:
                break
            build_ids = self._next_ids("builds", batch_size, last_id, missing_only)
            if not build_ids:
                break

            batches += 1
            total += len(build_ids)
            last_id = build_ids[-1]
            if dry_run:
                self.stdout.write(
                    f"Would process {len(build_ids)} builds through id={last_id}"
                )
                continue

            with transaction.atomic():
                self._upsert_build_definitions(build_ids)
                self._upsert_build_runs(build_ids)

            self.stdout.write(f"Processed {len(build_ids)} builds through id={last_id}")

        action = "Would process" if dry_run else "Processed"
        self.stdout.write(self.style.SUCCESS(f"{action} {total} builds"))

    def _backfill_tests(
        self,
        batch_size: int,
        resume_from_id: str,
        dry_run: bool,
        max_batches: int | None,
        missing_only: bool,
    ) -> None:
        total = 0
        batches = 0
        last_id = resume_from_id
        while True:
            if max_batches is not None and batches >= max_batches:
                break
            test_ids = self._next_ids("tests", batch_size, last_id, missing_only)
            if not test_ids:
                break

            batches += 1
            total += len(test_ids)
            last_id = test_ids[-1]
            if dry_run:
                self.stdout.write(
                    f"Would process {len(test_ids)} tests through id={last_id}"
                )
                continue

            with transaction.atomic():
                self._upsert_test_definitions(test_ids)
                self._upsert_test_runs(test_ids)

            self.stdout.write(f"Processed {len(test_ids)} tests through id={last_id}")

        action = "Would process" if dry_run else "Processed"
        self.stdout.write(self.style.SUCCESS(f"{action} {total} tests"))

    def _backfill_incidents(self, dry_run: bool) -> None:
        if dry_run:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) FILTER (
                            WHERE build_id IS NOT NULL AND build_run_id IS NULL
                        ) AS build_incidents,
                        COUNT(*) FILTER (
                            WHERE test_id IS NOT NULL AND test_run_id IS NULL
                        ) AS test_incidents
                    FROM incidents
                    """
                )
                build_incidents, test_incidents = cursor.fetchone()
            self.stdout.write(
                "Would link "
                f"{build_incidents} build incidents and {test_incidents} test incidents"
            )
            return

        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE incidents
                SET build_run_id = build_runs.id
                FROM build_runs
                WHERE build_id IS NOT NULL
                    AND build_run_id IS NULL
                    AND build_runs.kci_id = incidents.build_id
                """
            )
            build_count = cursor.rowcount
            cursor.execute(
                """
                UPDATE incidents
                SET test_run_id = test_runs.id
                FROM test_runs
                WHERE test_id IS NOT NULL
                    AND test_run_id IS NULL
                    AND test_runs.kci_id = incidents.test_id
                """
            )
            test_count = cursor.rowcount

        self.stdout.write(
            self.style.SUCCESS(
                f"Linked {build_count} build incidents and {test_count} test incidents"
            )
        )

    def _next_ids(
        self,
        table_name: Literal["builds", "tests"],
        batch_size: int,
        last_id: str,
        missing_only: bool,
    ):
        run_table_name = {
            "builds": "build_runs",
            "tests": "test_runs",
        }[table_name]
        run_id_column = "kci_id"
        eligible_clause = ""
        if table_name == "tests":
            eligible_clause = """
                AND EXISTS (
                    SELECT 1 FROM build_runs
                    WHERE build_runs.kci_id = tests.build_id
                )
            """
        missing_clause = ""
        if missing_only:
            missing_clause = f"""
                AND NOT EXISTS (
                    SELECT 1 FROM {run_table_name}
                    WHERE {run_table_name}.{run_id_column} = {table_name}.id
                )
            """

        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT id
                FROM {table_name}
                WHERE id > %s
                {eligible_clause}
                {missing_clause}
                ORDER BY id
                LIMIT %s
                """,
                [last_id, batch_size],
            )
            return [row[0] for row in cursor.fetchall()]

    def _upsert_build_definitions(self, build_ids: list[str]) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH selected_builds AS (
                    SELECT *
                    FROM builds
                    WHERE id = ANY(%s)
                ),
                latest_definitions AS (
                    SELECT DISTINCT ON (checkout_id, series)
                        _timestamp,
                        checkout_id,
                        origin,
                        architecture,
                        compiler,
                        config_name,
                        series
                    FROM selected_builds
                    ORDER BY checkout_id, series, _timestamp DESC NULLS LAST
                )
                INSERT INTO build_definitions (
                    _timestamp,
                    checkout_id,
                    origin,
                    architecture,
                    compiler,
                    config_name,
                    series
                )
                SELECT
                    _timestamp,
                    checkout_id,
                    origin,
                    architecture,
                    compiler,
                    config_name,
                    series
                FROM latest_definitions
                ON CONFLICT ON CONSTRAINT build_definitions_checkout_series_unique
                DO UPDATE SET
                    _timestamp = GREATEST(
                        build_definitions._timestamp,
                        EXCLUDED._timestamp
                    ),
                    origin = COALESCE(build_definitions.origin, EXCLUDED.origin),
                    architecture = COALESCE(
                        build_definitions.architecture,
                        EXCLUDED.architecture
                    ),
                    compiler = COALESCE(build_definitions.compiler, EXCLUDED.compiler),
                    config_name = COALESCE(
                        build_definitions.config_name,
                        EXCLUDED.config_name
                    )
                """,
                [build_ids],
            )

    def _upsert_build_runs(self, build_ids: list[str]) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO build_runs (
                    _timestamp,
                    kci_id,
                    build_definition_id,
                    checkout_id,
                    origin,
                    comment,
                    start_time,
                    duration,
                    command,
                    input_files,
                    output_files,
                    config_url,
                    log_url,
                    log_excerpt,
                    misc,
                    status
                )
                SELECT
                    builds._timestamp,
                    builds.id,
                    build_definitions.id,
                    builds.checkout_id,
                    builds.origin,
                    builds.comment,
                    builds.start_time,
                    builds.duration,
                    builds.command,
                    builds.input_files,
                    builds.output_files,
                    builds.config_url,
                    builds.log_url,
                    builds.log_excerpt,
                    builds.misc,
                    builds.status
                FROM builds
                JOIN build_definitions
                    ON build_definitions.checkout_id = builds.checkout_id
                    AND build_definitions.series = builds.series
                WHERE builds.id = ANY(%s)
                ON CONFLICT (kci_id)
                DO UPDATE SET
                    _timestamp = GREATEST(build_runs._timestamp, EXCLUDED._timestamp),
                    build_definition_id = COALESCE(
                        build_runs.build_definition_id,
                        EXCLUDED.build_definition_id
                    ),
                    checkout_id = COALESCE(build_runs.checkout_id, EXCLUDED.checkout_id),
                    origin = COALESCE(build_runs.origin, EXCLUDED.origin),
                    comment = COALESCE(build_runs.comment, EXCLUDED.comment),
                    start_time = COALESCE(build_runs.start_time, EXCLUDED.start_time),
                    duration = COALESCE(build_runs.duration, EXCLUDED.duration),
                    command = COALESCE(build_runs.command, EXCLUDED.command),
                    input_files = COALESCE(build_runs.input_files, EXCLUDED.input_files),
                    output_files = COALESCE(
                        build_runs.output_files,
                        EXCLUDED.output_files
                    ),
                    config_url = COALESCE(build_runs.config_url, EXCLUDED.config_url),
                    log_url = COALESCE(build_runs.log_url, EXCLUDED.log_url),
                    log_excerpt = COALESCE(
                        build_runs.log_excerpt,
                        EXCLUDED.log_excerpt
                    ),
                    misc = COALESCE(build_runs.misc, EXCLUDED.misc),
                    status = COALESCE(build_runs.status, EXCLUDED.status)
                """,
                [build_ids],
            )

    def _upsert_test_definitions(self, test_ids: list[str]) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH selected_tests AS (
                    SELECT tests.*, build_runs.build_definition_id
                    FROM tests
                    JOIN build_runs ON build_runs.kci_id = tests.build_id
                    WHERE tests.id = ANY(%s)
                ),
                latest_definitions AS (
                    SELECT DISTINCT ON (
                        build_definition_id,
                        path,
                        number_prefix,
                        number_unit
                    )
                        _timestamp,
                        build_definition_id,
                        path,
                        number_prefix,
                        number_unit
                    FROM selected_tests
                    ORDER BY
                        build_definition_id,
                        path,
                        number_prefix,
                        number_unit,
                        _timestamp DESC NULLS LAST
                )
                INSERT INTO test_definitions (
                    _timestamp,
                    build_definition_id,
                    path,
                    number_prefix,
                    number_unit
                )
                SELECT
                    _timestamp,
                    build_definition_id,
                    path,
                    number_prefix,
                    number_unit
                FROM latest_definitions
                ON CONFLICT ON CONSTRAINT test_definitions_build_path_number_unique
                DO UPDATE SET
                    _timestamp = GREATEST(
                        test_definitions._timestamp,
                        EXCLUDED._timestamp
                    )
                """,
                [test_ids],
            )

    def _upsert_test_runs(self, test_ids: list[str]) -> None:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO test_runs (
                    _timestamp,
                    kci_id,
                    test_definition_id,
                    build_run_id,
                    origin,
                    environment_comment,
                    environment_misc,
                    platform,
                    comment,
                    log_url,
                    log_excerpt,
                    status,
                    start_time,
                    duration,
                    input_files,
                    output_files,
                    misc,
                    number_value,
                    environment_compatible,
                    is_boot
                )
                SELECT
                    tests._timestamp,
                    tests.id,
                    test_definitions.id,
                    build_runs.id,
                    tests.origin,
                    tests.environment_comment,
                    tests.environment_misc,
                    tests.environment_misc ->> 'platform',
                    tests.comment,
                    tests.log_url,
                    tests.log_excerpt,
                    tests.status,
                    tests.start_time,
                    tests.duration,
                    tests.input_files,
                    tests.output_files,
                    tests.misc,
                    tests.number_value,
                    tests.environment_compatible,
                    CASE
                        WHEN tests.path = 'boot' OR tests.path LIKE 'boot.%%' THEN TRUE
                        WHEN tests.path IS NULL THEN NULL
                        ELSE FALSE
                    END
                FROM tests
                JOIN build_runs ON build_runs.kci_id = tests.build_id
                JOIN test_definitions
                    ON test_definitions.build_definition_id =
                        build_runs.build_definition_id
                    AND test_definitions.path IS NOT DISTINCT FROM tests.path
                    AND test_definitions.number_prefix IS NOT DISTINCT FROM
                        tests.number_prefix
                    AND test_definitions.number_unit IS NOT DISTINCT FROM
                        tests.number_unit
                WHERE tests.id = ANY(%s)
                ON CONFLICT (kci_id)
                DO UPDATE SET
                    _timestamp = GREATEST(test_runs._timestamp, EXCLUDED._timestamp),
                    test_definition_id = COALESCE(
                        test_runs.test_definition_id,
                        EXCLUDED.test_definition_id
                    ),
                    build_run_id = COALESCE(test_runs.build_run_id, EXCLUDED.build_run_id),
                    origin = COALESCE(test_runs.origin, EXCLUDED.origin),
                    environment_comment = COALESCE(
                        test_runs.environment_comment,
                        EXCLUDED.environment_comment
                    ),
                    environment_misc = COALESCE(
                        test_runs.environment_misc,
                        EXCLUDED.environment_misc
                    ),
                    platform = COALESCE(test_runs.platform, EXCLUDED.platform),
                    comment = COALESCE(test_runs.comment, EXCLUDED.comment),
                    log_url = COALESCE(test_runs.log_url, EXCLUDED.log_url),
                    log_excerpt = COALESCE(
                        test_runs.log_excerpt,
                        EXCLUDED.log_excerpt
                    ),
                    status = COALESCE(test_runs.status, EXCLUDED.status),
                    start_time = COALESCE(test_runs.start_time, EXCLUDED.start_time),
                    duration = COALESCE(test_runs.duration, EXCLUDED.duration),
                    input_files = COALESCE(test_runs.input_files, EXCLUDED.input_files),
                    output_files = COALESCE(test_runs.output_files, EXCLUDED.output_files),
                    misc = COALESCE(test_runs.misc, EXCLUDED.misc),
                    number_value = COALESCE(test_runs.number_value, EXCLUDED.number_value),
                    environment_compatible = COALESCE(
                        test_runs.environment_compatible,
                        EXCLUDED.environment_compatible
                    ),
                    is_boot = COALESCE(test_runs.is_boot, EXCLUDED.is_boot)
                """,
                [test_ids],
            )
