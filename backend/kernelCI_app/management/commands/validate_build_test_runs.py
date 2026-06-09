from typing import Literal

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

Phase = Literal["all", "builds", "tests", "incidents"]


class Command(BaseCommand):
    help = "Validate build/test definition and run table backfills."

    def add_arguments(self, parser):
        parser.add_argument(
            "--phase",
            choices=["all", "builds", "tests", "incidents"],
            default="all",
        )
        parser.add_argument("--sample-size", type=int, default=20)
        parser.add_argument("--fail-on-mismatch", action="store_true")

    def handle(self, *args, **options):
        sample_size = options["sample_size"]
        if sample_size < 0:
            raise CommandError("--sample-size must be zero or greater")

        with connection.cursor() as cursor:
            cursor.execute("SET max_parallel_workers_per_gather = 0")

        phase: Phase = options["phase"]
        has_mismatch = False

        if phase in {"all", "builds"}:
            has_mismatch |= self._validate_builds(sample_size)
        if phase in {"all", "tests"}:
            has_mismatch |= self._validate_tests(sample_size)
        if phase in {"all", "incidents"}:
            has_mismatch |= self._validate_incidents()

        if has_mismatch and options["fail_on_mismatch"]:
            raise CommandError("Build/test run validation failed")

        if has_mismatch:
            self.stdout.write(self.style.WARNING("Build/test run validation failed"))
        else:
            self.stdout.write(self.style.SUCCESS("Build/test run validation passed"))

    def _validate_builds(self, sample_size: int) -> bool:
        summary = self._fetch_one(
            """
            SELECT
                COUNT(*) AS legacy_builds,
                COUNT(*) FILTER (WHERE checkouts.id IS NOT NULL) AS eligible_builds,
                COUNT(*) FILTER (WHERE checkouts.id IS NULL) AS orphan_builds,
                (SELECT COUNT(*) FROM build_runs) AS build_runs,
                (SELECT COUNT(*) FROM build_run_payloads) AS build_run_payloads,
                COUNT(*) FILTER (
                    WHERE checkouts.id IS NOT NULL
                        AND build_runs.kci_id IS NULL
                ) AS missing_runs,
                COUNT(*) FILTER (
                    WHERE build_runs.kci_id IS NOT NULL
                        AND build_run_payloads.build_run_id IS NULL
                ) AS missing_payloads,
                COUNT(*) FILTER (
                    WHERE build_runs.kci_id IS NOT NULL
                        AND NOT (
                            build_runs.checkout_id = checkouts.id
                            AND build_definitions.checkout_id = checkouts.id
                            AND build_definitions.series = builds.series
                            AND build_runs.commit_id IS NOT DISTINCT FROM
                                checkouts.commit_id
                        )
                ) AS mismatched_runs
            FROM builds
            LEFT JOIN build_runs ON build_runs.kci_id = builds.id
            LEFT JOIN build_definitions
                ON build_definitions.id = build_runs.build_definition_id
            LEFT JOIN checkouts ON checkouts.kci_id = builds.checkout_id
            LEFT JOIN build_run_payloads
                ON build_run_payloads.build_run_id = build_runs.id
            """
        )
        self._print_summary("builds", summary)
        samples = self._fetch_samples(
            """
            SELECT
                builds.id AS legacy_id,
                builds.checkout_id AS legacy_checkout_kci_id,
                checkouts.id AS checkout_id,
                builds.series AS legacy_series,
                build_runs.kci_id AS run_kci_id,
                build_runs.checkout_id AS run_checkout_id,
                build_runs.commit_id AS run_commit_id,
                build_run_payloads.build_run_id AS payload_build_run_id,
                build_definitions.checkout_id AS definition_checkout_id,
                build_definitions.series AS definition_series,
                checkouts.commit_id AS checkout_commit_id
            FROM builds
            LEFT JOIN build_runs ON build_runs.kci_id = builds.id
            LEFT JOIN build_definitions
                ON build_definitions.id = build_runs.build_definition_id
            LEFT JOIN checkouts ON checkouts.kci_id = builds.checkout_id
            LEFT JOIN build_run_payloads
                ON build_run_payloads.build_run_id = build_runs.id
            WHERE checkouts.id IS NOT NULL
                AND (
                    build_runs.kci_id IS NULL
                    OR build_run_payloads.build_run_id IS NULL
                    OR NOT (
                        build_runs.checkout_id = checkouts.id
                        AND build_definitions.checkout_id = checkouts.id
                        AND build_definitions.series = builds.series
                        AND build_runs.commit_id IS NOT DISTINCT FROM checkouts.commit_id
                    )
                )
            ORDER BY builds.id
            LIMIT %s
            """,
            sample_size,
        )
        self._print_samples("build samples", samples)
        return bool(
            summary["missing_runs"]
            or summary["missing_payloads"]
            or summary["mismatched_runs"]
        )

    def _validate_tests(self, sample_size: int) -> bool:
        summary = self._fetch_one(
            """
            SELECT
                COUNT(*) AS legacy_tests,
                COUNT(*) FILTER (WHERE build_runs.kci_id IS NOT NULL) AS eligible_tests,
                COUNT(*) FILTER (WHERE build_runs.kci_id IS NULL) AS orphan_tests,
                (SELECT COUNT(*) FROM test_runs) AS test_runs,
                (SELECT COUNT(*) FROM test_run_payloads) AS test_run_payloads,
                (
                    SELECT COALESCE(SUM(cardinality(t.environment_compatible)), 0)
                    FROM tests t
                    JOIN build_runs br ON br.kci_id = t.build_id
                ) AS expected_hardware_links,
                (SELECT COUNT(*) FROM test_run_hardwares) AS test_run_hardwares,
                COUNT(*) FILTER (
                    WHERE build_runs.kci_id IS NOT NULL
                        AND test_runs.kci_id IS NULL
                ) AS missing_runs,
                COUNT(*) FILTER (
                    WHERE build_runs.kci_id IS NOT NULL
                        AND test_runs.kci_id IS NOT NULL
                        AND test_run_payloads.test_run_id IS NULL
                ) AS missing_payloads,
                (
                    SELECT COUNT(*)
                    FROM tests t
                    JOIN build_runs br ON br.kci_id = t.build_id
                    JOIN test_runs tr ON tr.kci_id = t.id
                    CROSS JOIN LATERAL unnest(t.environment_compatible) AS compat(
                        hardware
                    )
                    LEFT JOIN hardwares h ON h.name = compat.hardware
                    LEFT JOIN test_run_hardwares trh
                        ON trh.test_run_id = tr.id
                        AND trh.hardware_id = h.id
                    WHERE compat.hardware IS NOT NULL
                        AND trh.test_run_id IS NULL
                ) AS missing_hardware_links,
                COUNT(*) FILTER (
                    WHERE test_runs.kci_id IS NOT NULL
                        AND NOT (
                            test_runs.build_run_id = build_runs.id
                            AND test_runs.checkout_id = build_runs.checkout_id
                            AND test_definitions.path IS NOT DISTINCT FROM tests.path
                            AND test_definitions.number_prefix IS NOT DISTINCT FROM
                                tests.number_prefix
                            AND test_definitions.number_unit IS NOT DISTINCT FROM
                                tests.number_unit
                        )
                ) AS mismatched_runs
            FROM tests
            LEFT JOIN build_runs ON build_runs.kci_id = tests.build_id
            LEFT JOIN test_runs ON test_runs.kci_id = tests.id
            LEFT JOIN test_definitions
                ON test_definitions.id = test_runs.test_definition_id
            LEFT JOIN test_run_payloads
                ON test_run_payloads.test_run_id = test_runs.id
            """
        )
        self._print_summary("tests", summary)
        samples = self._fetch_samples(
            """
            SELECT
                tests.id AS legacy_id,
                tests.build_id AS legacy_build_id,
                tests.path AS legacy_path,
                test_runs.kci_id AS run_kci_id,
                test_runs.build_run_id AS run_build_id,
                test_runs.checkout_id AS run_checkout_id,
                test_run_payloads.test_run_id AS payload_test_run_id,
                compatible_hardware.hardware AS missing_hardware,
                build_runs.checkout_id AS build_checkout_id,
                test_definitions.path AS definition_path
            FROM tests
            LEFT JOIN build_runs ON build_runs.kci_id = tests.build_id
            LEFT JOIN test_runs ON test_runs.kci_id = tests.id
            LEFT JOIN test_definitions
                ON test_definitions.id = test_runs.test_definition_id
            LEFT JOIN test_run_payloads
                ON test_run_payloads.test_run_id = test_runs.id
            LEFT JOIN LATERAL unnest(tests.environment_compatible) AS compatible_hardware(
                hardware
            ) ON TRUE
            LEFT JOIN hardwares
                ON hardwares.name = compatible_hardware.hardware
            LEFT JOIN test_run_hardwares
                ON test_run_hardwares.test_run_id = test_runs.id
                AND test_run_hardwares.hardware_id = hardwares.id
            WHERE build_runs.kci_id IS NOT NULL
                AND (
                    test_runs.kci_id IS NULL
                    OR test_run_payloads.test_run_id IS NULL
                    OR (
                        compatible_hardware.hardware IS NOT NULL
                        AND test_run_hardwares.test_run_id IS NULL
                    )
                    OR NOT (
                        test_runs.build_run_id = build_runs.id
                        AND test_runs.checkout_id = build_runs.checkout_id
                        AND test_definitions.path IS NOT DISTINCT FROM tests.path
                        AND test_definitions.number_prefix IS NOT DISTINCT FROM
                            tests.number_prefix
                        AND test_definitions.number_unit IS NOT DISTINCT FROM
                            tests.number_unit
                    )
                )
            ORDER BY tests.id
            LIMIT %s
            """,
            sample_size,
        )
        self._print_samples("test samples", samples)
        return bool(
            summary["missing_runs"]
            or summary["missing_payloads"]
            or summary["missing_hardware_links"]
            or summary["mismatched_runs"]
        )

    def _validate_incidents(self) -> bool:
        summary = self._fetch_one(
            """
            SELECT
                COUNT(*) FILTER (WHERE incidents.build_id IS NOT NULL) AS build_incidents,
                COUNT(*) FILTER (
                    WHERE incidents.build_id IS NOT NULL AND build_runs.kci_id IS NOT NULL
                ) AS eligible_build_incidents,
                COUNT(*) FILTER (
                    WHERE incidents.build_id IS NOT NULL
                        AND incidents.build_run_id = build_runs.id
                ) AS linked_build_incidents,
                COUNT(*) FILTER (
                    WHERE incidents.build_id IS NOT NULL
                        AND incidents.build_run_id IS DISTINCT FROM build_runs.id
                        AND build_runs.kci_id IS NOT NULL
                ) AS missing_build_run_links,
                COUNT(*) FILTER (
                    WHERE incidents.build_id IS NOT NULL AND build_runs.kci_id IS NULL
                ) AS orphan_build_incidents,
                COUNT(*) FILTER (WHERE incidents.test_id IS NOT NULL) AS test_incidents,
                COUNT(*) FILTER (
                    WHERE incidents.test_id IS NOT NULL AND test_runs.kci_id IS NOT NULL
                ) AS eligible_test_incidents,
                COUNT(*) FILTER (
                    WHERE incidents.test_id IS NOT NULL
                        AND incidents.test_run_id = test_runs.id
                ) AS linked_test_incidents,
                COUNT(*) FILTER (
                    WHERE incidents.test_id IS NOT NULL
                        AND incidents.test_run_id IS DISTINCT FROM test_runs.id
                        AND test_runs.kci_id IS NOT NULL
                ) AS missing_test_run_links,
                COUNT(*) FILTER (
                    WHERE incidents.test_id IS NOT NULL AND test_runs.kci_id IS NULL
                ) AS orphan_test_incidents
            FROM incidents
            LEFT JOIN build_runs ON build_runs.kci_id = incidents.build_id
            LEFT JOIN test_runs ON test_runs.kci_id = incidents.test_id
            """
        )
        self._print_summary("incidents", summary)
        return bool(
            summary["missing_build_run_links"] or summary["missing_test_run_links"]
        )

    def _fetch_one(self, sql: str) -> dict[str, int]:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            columns = [column[0] for column in cursor.description]
            row = cursor.fetchone()
        return dict(zip(columns, row, strict=False))

    def _fetch_samples(self, sql: str, sample_size: int) -> list[dict]:
        if sample_size == 0:
            return []
        with connection.cursor() as cursor:
            cursor.execute(sql, [sample_size])
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]

    def _print_summary(self, label: str, summary: dict[str, int]) -> None:
        self.stdout.write(label)
        for key, value in summary.items():
            self.stdout.write(f"  {key}={value}")

    def _print_samples(self, label: str, samples: list[dict]) -> None:
        if not samples:
            return
        self.stdout.write(label)
        for sample in samples:
            self.stdout.write(f"  {sample}")
