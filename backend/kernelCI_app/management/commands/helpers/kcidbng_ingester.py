import json
import logging
import multiprocessing
import os
import time
import traceback
from multiprocessing.sharedctypes import Synchronized
from multiprocessing.synchronize import Lock as ProcessLock
from queue import Queue
from typing import Any, Optional, TypedDict

import kcidb_io
from django.db import connections, transaction
from prometheus_client import Counter
from typing_extensions import Literal

from kernelCI_app.constants.ingester import (
    AUTOMATIC_LAB_FIELD,
    AUTOMATIC_LABS,
    CONVERT_LOG_EXCERPT,
    INGEST_BATCH_SIZE,
    INGEST_FILES_BATCH_SIZE,
    INGEST_QUEUE_MAXSIZE,
    INGESTER_GRAFANA_LABEL,
    VERBOSE,
)
from kernelCI_app.helpers.logger import out
from kernelCI_app.management.commands.generated.insert_queries import INSERT_QUERIES
from kernelCI_app.management.commands.helpers.aggregation_helpers import (
    aggregate_checkouts_and_pendings,
)
from kernelCI_app.management.commands.helpers.commit_enrichment import (
    CommitEnrichment,
    enrich_commit_checkouts,
)
from kernelCI_app.management.commands.helpers.file_utils import move_file_to_failed_dir
from kernelCI_app.management.commands.helpers.log_excerpt_utils import (
    extract_log_excerpt,
)
from kernelCI_app.management.commands.helpers.process_submissions import (
    TableNames,
    build_instances_from_submission,
)
from kernelCI_app.models import (
    BuildDefinitions,
    BuildRunPayloads,
    BuildRuns,
    Builds,
    Checkouts,
    Commits,
    Incidents,
    Issues,
    TestDefinitions,
    TestRunPayloads,
    TestRuns,
    Tests,
)
from kernelCI_app.typeModels.modelTypes import TableModels

type INGESTER_DIRS = Literal["archive", "failed", "pending_retry"]


class SubmissionFileMetadata(TypedDict):
    path: str
    name: str
    size: int


logger = logging.getLogger("ingester")


FILES_INGESTER_COUNTER = Counter(
    "kcidb_ingestions", "Number of files ingested", ["ingester"]
)

CHECKOUTS_COUNTER = Counter(
    "kcidb_checkouts", "Number of checkouts ingested", ["ingester", "origin"]
)
ISSUES_COUNTER = Counter(
    "kcidb_issues", "Number of issues ingested", ["ingester", "origin"]
)
BUILDS_COUNTER = Counter(
    "kcidb_builds", "Number of builds ingested", ["ingester", "origin", "lab"]
)
TESTS_COUNTER = Counter(
    "kcidb_tests", "Number of tests ingested", ["ingester", "origin", "lab", "platform"]
)
INCIDENTS_COUNTER = Counter(
    "kcidb_incidents", "Number of incidents ingested", ["ingester", "origin"]
)


def standardize_tree_names(
    input_data: dict[str, Any], tree_names: dict[str, str]
) -> None:
    """
    Standardize tree names in input data using the provided mapping
    """

    checkouts: list[dict[str, Any]] = input_data.get("checkouts", [])

    for checkout in checkouts:
        git_url = checkout.get("git_repository_url")
        if git_url in tree_names:
            correct_tree = tree_names[git_url]
            if checkout.get("tree_name") != correct_tree:
                checkout["tree_name"] = correct_tree


def _standardize_lab_field(item: dict[str, Any], field: str) -> None:
    """
    Moves automatic lab/runtime value to AUTOMATIC_LAB_FIELD and falls back to origin.

        lab is AUTOMATIC_LAB -> fallback to origin
        lab is None -> fallback to origin
        lab is not None -> do nothing
    """
    lab = item.get("misc", {}).get(field)
    is_automatic = lab and AUTOMATIC_LABS.match(lab)
    if is_automatic:
        item["misc"][AUTOMATIC_LAB_FIELD] = lab
        item["misc"].pop(field, None)
        lab = None

    if not lab or is_automatic:
        origin = item.get("origin")
        if origin:
            item.setdefault("misc", {})[field] = origin


def standardize_labs(input_data: dict[str, Any]) -> None:
    """
    Standardize labs in data, moving automatic lab names to AUTOMATIC_LAB_FIELD.
    Falls back to 'origin' when 'lab' (builds) or 'runtime' (tests) is missing.
    """
    for build in input_data.get("builds", []):
        _standardize_lab_field(build, "lab")

    for test in input_data.get("tests", []):
        _standardize_lab_field(test, "runtime")


def _extract_origins_info(data: Optional[dict[str, Any]]) -> str:
    """Extract unique origins from submissions for error reporting.

    Returns a formatted string like " [origins: origin1, origin2]" if origins
    are found, otherwise an empty string.
    """
    origins: set[str] = set()
    if data:
        for section in ("tests", "builds", "checkouts", "issues", "incidents"):
            for item in data.get(section, []):
                origin = item.get("origin")
                if origin:
                    origins.add(origin)
    return f" [origins: {', '.join(sorted(origins))}]" if origins else ""


def prepare_file_data(
    file: SubmissionFileMetadata, tree_names: dict[str, str]
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    """
    Prepare file data: read, extract log excerpts, standardize tree names, validate.
    This function does everything except the actual database load.

    Returns `data, metadata`.
    If an error happens, `data` will be None; if file is empty, both are None.
    """
    fsize = file["size"]

    if fsize == 0:
        if VERBOSE:
            logger.info("File %s is empty, skipping, deleting", file["path"])
        os.remove(file["path"])
        return None, None

    start_time = time.time()
    if VERBOSE:
        logger.info("Processing file %s, size: %d", file["name"], fsize)

    data: Optional[dict[str, Any]] = None
    try:
        with open(file["path"], "r") as f:
            data = json.loads(f.read())

        # These operations can be done in parallel (especially extract_log_excerpt)
        if CONVERT_LOG_EXCERPT:
            extract_log_excerpt(data)
        standardize_tree_names(data, tree_names)
        kcidb_io.schema.V5_3.validate(data)
        kcidb_io.schema.V5_3.upgrade(data)
        standardize_labs(data)
        commit_enrichments = enrich_commit_checkouts(data.get("checkouts", []))

        processing_time = time.time() - start_time
        return data, {
            "fsize": fsize,
            "processing_time": processing_time,
            "commit_enrichments": commit_enrichments,
        }
    except Exception as e:
        origin_info = _extract_origins_info(data)
        logger.error("Error preparing data from %s%s: %s", file["name"], origin_info, e)
        logger.error(traceback.format_exc())
        return None, {
            "error": str(e),
        }


def consume_buffer(buffer: list[TableModels], table_name: TableNames) -> None:
    """
    Consume a buffer of items and insert them into the database.
    This function is called by the db_worker thread.
    """
    if not buffer:
        return

    insert_props = INSERT_QUERIES[table_name]
    updateable_model_fields = insert_props["updateable_model_fields"]
    query = insert_props["query"]

    params = []
    for obj in buffer:
        obj_values = []
        for field in updateable_model_fields:
            value = getattr(obj, field)
            model_field = obj._meta.get_field(field)
            if model_field.get_internal_type() == "JSONField" and value is not None:
                value = json.dumps(value)
            obj_values.append(value)
        params.append(tuple(obj_values))

    t0 = time.time()
    with connections["default"].cursor() as cursor:
        cursor.executemany(query, params)

    out("bulk_create %s: n=%d in %.3fs" % (table_name, len(buffer), time.time() - t0))


def _commit_context_key_from_checkout(
    checkout: Checkouts,
) -> tuple[str | None, str | None, str | None, str] | None:
    if not isinstance(checkout.git_commit_hash, str) or not checkout.git_commit_hash:
        return None
    return (
        checkout.tree_name,
        checkout.git_repository_url,
        checkout.git_repository_branch,
        checkout.git_commit_hash,
    )


def _commit_context_key_from_row(
    row: tuple[int, str | None, str | None, str | None, str],
) -> tuple[str | None, str | None, str | None, str]:
    _, tree_name, git_repository_url, git_repository_branch, git_commit_hash = row
    return (tree_name, git_repository_url, git_repository_branch, git_commit_hash)


def assign_commit_ids(checkouts_buf: list[Checkouts]) -> None:
    """Resolve nullable checkout.commit_id after commit rows have been upserted."""
    checkout_keys = [
        key
        for checkout in checkouts_buf
        if (key := _commit_context_key_from_checkout(checkout)) is not None
    ]
    commit_hashes = {key[3] for key in checkout_keys}
    if not commit_hashes:
        return

    commit_rows = Commits.objects.filter(git_commit_hash__in=commit_hashes).values_list(
        "id",
        "tree_name",
        "git_repository_url",
        "git_repository_branch",
        "git_commit_hash",
    )
    commit_ids_by_key = {
        _commit_context_key_from_row(row): row[0] for row in commit_rows
    }

    for checkout in checkouts_buf:
        key = _commit_context_key_from_checkout(checkout)
        if key is not None:
            checkout.commit_id = commit_ids_by_key.get(key)


def assign_build_definition_ids(build_runs_buf: list[BuildRuns]) -> None:
    """Resolve build_run.build_definition_id after definitions are upserted."""
    build_keys = {
        (build_run.checkout_id, getattr(build_run, "_definition_series", None))
        for build_run in build_runs_buf
        if build_run.checkout_id and getattr(build_run, "_definition_series", None)
    }
    checkout_ids = {key[0] for key in build_keys}
    series_values = {key[1] for key in build_keys}
    if not checkout_ids or not series_values:
        return

    rows = BuildDefinitions.objects.filter(
        checkout_id__in=checkout_ids,
        series__in=series_values,
    ).values_list("id", "checkout_id", "series")
    definition_ids_by_key = {
        (checkout_id, series): id for id, checkout_id, series in rows
    }

    for build_run in build_runs_buf:
        build_run.build_definition_id = definition_ids_by_key.get(
            (build_run.checkout_id, getattr(build_run, "_definition_series", None))
        )


def assign_build_commit_ids(build_runs_buf: list[BuildRuns]) -> None:
    """Denormalize checkout.commit_id onto build_runs for commit-metadata joins."""
    checkout_ids = {build_run.checkout_id for build_run in build_runs_buf}
    if not checkout_ids:
        return

    commit_ids_by_checkout_id = dict(
        Checkouts.objects.filter(id__in=checkout_ids).values_list("id", "commit_id")
    )

    for build_run in build_runs_buf:
        build_run.commit_id = commit_ids_by_checkout_id.get(build_run.checkout_id)


def assign_test_build_definition_ids(
    test_definitions_buf: list[TestDefinitions],
    test_runs_buf: list[TestRuns],
) -> None:
    """Resolve test_definition.build_definition_id from the linked build run."""
    build_run_kci_ids = {
        getattr(test_run, "_legacy_build_id", None)
        for test_run in test_runs_buf
        if getattr(test_run, "_legacy_build_id", None)
    }
    build_run_kci_ids.update(
        getattr(test_definition, "_legacy_build_id", None)
        for test_definition in test_definitions_buf
        if getattr(test_definition, "_legacy_build_id", None)
    )
    if not build_run_kci_ids:
        return

    rows = BuildRuns.objects.filter(kci_id__in=build_run_kci_ids).values_list(
        "kci_id", "build_definition_id"
    )
    build_definition_id_by_build_run = dict(rows)

    for test_definition in test_definitions_buf:
        legacy_build_id = getattr(test_definition, "_legacy_build_id", None)
        test_definition.build_definition_id = build_definition_id_by_build_run.get(
            legacy_build_id
        )


def assign_test_definition_ids(test_runs_buf: list[TestRuns]) -> None:
    """Resolve test_run.test_definition_id after test definitions are upserted."""
    build_run_kci_ids = {
        getattr(test_run, "_legacy_build_id", None)
        for test_run in test_runs_buf
        if getattr(test_run, "_legacy_build_id", None)
    }
    if not build_run_kci_ids:
        return

    build_rows = BuildRuns.objects.filter(kci_id__in=build_run_kci_ids).values_list(
        "kci_id", "id", "build_definition_id", "checkout_id"
    )
    build_run_by_kci_id = {
        kci_id: (id, build_definition_id, checkout_id)
        for kci_id, id, build_definition_id, checkout_id in build_rows
    }
    build_definition_ids = {
        build_definition_id
        for _, build_definition_id, _ in build_run_by_kci_id.values()
    }
    if not build_definition_ids:
        return

    rows = TestDefinitions.objects.filter(
        build_definition_id__in=build_definition_ids,
    ).values_list("id", "build_definition_id", "path", "number_prefix", "number_unit")
    definition_ids_by_key = {
        (build_definition_id, path, number_prefix, number_unit): id
        for id, build_definition_id, path, number_prefix, number_unit in rows
    }

    for test_run in test_runs_buf:
        build_run = build_run_by_kci_id.get(getattr(test_run, "_legacy_build_id", None))
        if build_run is None:
            continue
        build_run_id, build_definition_id, checkout_id = build_run
        test_run.checkout_id = checkout_id
        test_run.build_run_id = build_run_id
        key = (
            build_definition_id,
            getattr(test_run, "_definition_path", None),
            getattr(test_run, "_definition_number_prefix", None),
            getattr(test_run, "_definition_number_unit", None),
        )
        test_run.test_definition_id = definition_ids_by_key.get(key)


def assign_incident_run_ids(incidents_buf: list[Incidents]) -> None:
    """Resolve incident run FKs from legacy KCIDB ids."""
    build_kci_ids = {
        incident.build_id for incident in incidents_buf if incident.build_id
    }
    test_kci_ids = {incident.test_id for incident in incidents_buf if incident.test_id}

    build_run_ids_by_kci_id = dict(
        BuildRuns.objects.filter(kci_id__in=build_kci_ids).values_list("kci_id", "id")
    )
    test_run_ids_by_kci_id = dict(
        TestRuns.objects.filter(kci_id__in=test_kci_ids).values_list("kci_id", "id")
    )

    for incident in incidents_buf:
        if incident.build_id:
            incident.build_run_id = build_run_ids_by_kci_id.get(incident.build_id)
        if incident.test_id:
            incident.test_run_id = test_run_ids_by_kci_id.get(incident.test_id)


def assign_build_payload_run_ids(payloads_buf: list[BuildRunPayloads]) -> None:
    build_kci_ids = {
        getattr(payload, "_legacy_build_id", None)
        for payload in payloads_buf
        if getattr(payload, "_legacy_build_id", None)
    }
    if not build_kci_ids:
        return

    build_run_ids_by_kci_id = dict(
        BuildRuns.objects.filter(kci_id__in=build_kci_ids).values_list("kci_id", "id")
    )
    for payload in payloads_buf:
        payload.build_run_id = build_run_ids_by_kci_id.get(
            getattr(payload, "_legacy_build_id", None)
        )


def assign_test_payload_run_ids(payloads_buf: list[TestRunPayloads]) -> None:
    test_kci_ids = {
        getattr(payload, "_legacy_test_id", None)
        for payload in payloads_buf
        if getattr(payload, "_legacy_test_id", None)
    }
    if not test_kci_ids:
        return

    test_run_ids_by_kci_id = dict(
        TestRuns.objects.filter(kci_id__in=test_kci_ids).values_list("kci_id", "id")
    )
    for payload in payloads_buf:
        payload.test_run_id = test_run_ids_by_kci_id.get(
            getattr(payload, "_legacy_test_id", None)
        )


def flush_buffers(
    *,
    commits_buf: list[Commits] | None = None,
    issues_buf: list[Issues],
    checkouts_buf: list[Checkouts],
    build_definitions_buf: list[BuildDefinitions] | None = None,
    builds_buf: list[Builds],
    build_runs_buf: list[BuildRuns] | None = None,
    build_run_payloads_buf: list[BuildRunPayloads] | None = None,
    test_definitions_buf: list[TestDefinitions] | None = None,
    tests_buf: list[Tests],
    test_runs_buf: list[TestRuns] | None = None,
    test_run_payloads_buf: list[TestRunPayloads] | None = None,
    incidents_buf: list[Incidents],
    buffer_files: set[tuple[str, str]],
    dirs: dict[INGESTER_DIRS, str],
    stat_ok: Synchronized,
    stat_fail: Synchronized,
    counter_lock: ProcessLock,
) -> None:
    """
    Consumes the list of objects and tries to insert them into the database.
    """
    if commits_buf is None:
        commits_buf = []
    if build_definitions_buf is None:
        build_definitions_buf = []
    if build_runs_buf is None:
        build_runs_buf = []
    if build_run_payloads_buf is None:
        build_run_payloads_buf = []
    if test_definitions_buf is None:
        test_definitions_buf = []
    if test_runs_buf is None:
        test_runs_buf = []
    if test_run_payloads_buf is None:
        test_run_payloads_buf = []

    total = (
        len(commits_buf)
        + len(issues_buf)
        + len(checkouts_buf)
        + len(build_definitions_buf)
        + len(builds_buf)
        + len(build_runs_buf)
        + len(build_run_payloads_buf)
        + len(test_definitions_buf)
        + len(tests_buf)
        + len(test_runs_buf)
        + len(test_run_payloads_buf)
        + len(incidents_buf)
    )

    if total == 0:
        return

    # Insert in dependency-safe order
    flush_start = time.time()
    try:
        # Single transaction for all tables in the flush
        with transaction.atomic():
            if commits_buf:
                consume_buffer(commits_buf, "commits")
            assign_commit_ids(checkouts_buf)
            consume_buffer(issues_buf, "issues")
            consume_buffer(checkouts_buf, "checkouts")
            consume_buffer(build_definitions_buf, "build_definitions")
            assign_build_definition_ids(build_runs_buf)
            assign_build_commit_ids(build_runs_buf)
            consume_buffer(builds_buf, "builds")
            consume_buffer(build_runs_buf, "build_runs")
            assign_build_payload_run_ids(build_run_payloads_buf)
            consume_buffer(build_run_payloads_buf, "build_run_payloads")
            assign_test_build_definition_ids(test_definitions_buf, test_runs_buf)
            consume_buffer(test_definitions_buf, "test_definitions")
            assign_test_definition_ids(test_runs_buf)
            consume_buffer(tests_buf, "tests")
            consume_buffer(test_runs_buf, "test_runs")
            assign_test_payload_run_ids(test_run_payloads_buf)
            consume_buffer(test_run_payloads_buf, "test_run_payloads")
            assign_incident_run_ids(incidents_buf)
            consume_buffer(incidents_buf, "incidents")
            aggregate_checkouts_and_pendings(
                checkouts_instances=checkouts_buf,
                tests_instances=tests_buf,
                build_instances=builds_buf,
            )
        for filename, filepath in buffer_files:
            os.rename(filepath, os.path.join(dirs["archive"], filename))

        with counter_lock:
            stat_ok.value += len(buffer_files)
    except Exception as e:
        logger.error("Error during buffer flush: %s", e)
        try:
            for filename, filepath in buffer_files:
                os.rename(filepath, os.path.join(dirs["failed"], filename))
            out("Moved %d files to pending retry directory" % len(buffer_files))
            with counter_lock:
                stat_fail.value += len(buffer_files)
        except OSError as oe:
            logger.error("OS error during buffer file pending retry move: %s", oe)
            logger.error("Removing files from buffer set, they should be retried")
    finally:
        flush_dur = time.time() - flush_start
        rate = total / flush_dur if flush_dur > 0 else 0.0
        msg = (
            "Flushed batch in %.3fs (%.1f items/s): "
            "commits=%d issues=%d checkouts=%d build_definitions=%d builds=%d "
            "build_runs=%d build_run_payloads=%d test_definitions=%d tests=%d "
            "test_runs=%d test_run_payloads=%d incidents=%d"
            % (
                flush_dur,
                rate,
                len(commits_buf),
                len(issues_buf),
                len(checkouts_buf),
                len(build_definitions_buf),
                len(builds_buf),
                len(build_runs_buf),
                len(build_run_payloads_buf),
                len(test_definitions_buf),
                len(tests_buf),
                len(test_runs_buf),
                len(test_run_payloads_buf),
                len(incidents_buf),
            )
        )
        out(msg)
        commits_buf.clear()
        issues_buf.clear()
        checkouts_buf.clear()
        build_definitions_buf.clear()
        builds_buf.clear()
        build_runs_buf.clear()
        build_run_payloads_buf.clear()
        test_definitions_buf.clear()
        tests_buf.clear()
        test_runs_buf.clear()
        test_run_payloads_buf.clear()
        incidents_buf.clear()
        buffer_files.clear()


MAP_TABLENAMES_TO_COUNTER: dict[TableNames, Counter] = {
    "checkouts": CHECKOUTS_COUNTER,
    "issues": ISSUES_COUNTER,
    "builds": BUILDS_COUNTER,
    "tests": TESTS_COUNTER,
    "incidents": INCIDENTS_COUNTER,
}


class SubmissionsInstances(TypedDict):
    commits: list[Commits]
    issues: list[Issues]
    checkouts: list[Checkouts]
    build_definitions: list[BuildDefinitions]
    builds: list[Builds]
    build_runs: list[BuildRuns]
    build_run_payloads: list[BuildRunPayloads]
    test_definitions: list[TestDefinitions]
    tests: list[Tests]
    test_runs: list[TestRuns]
    test_run_payloads: list[TestRunPayloads]
    incidents: list[Incidents]


def process_batch(
    process_queue: Queue,
    tree_names: dict[str, str],
    dirs: dict[INGESTER_DIRS, str],
    processed: Synchronized,
    stat_ok: Synchronized,
    stat_fail: Synchronized,
    counter_lock: ProcessLock,
) -> None:
    # Ensure that the new process has a unique connection to the database
    connections.close_all()

    instances_dict: SubmissionsInstances = {
        "commits": [],
        "issues": [],
        "checkouts": [],
        "build_definitions": [],
        "builds": [],
        "build_runs": [],
        "build_run_payloads": [],
        "test_definitions": [],
        "tests": [],
        "test_runs": [],
        "test_run_payloads": [],
        "incidents": [],
    }

    buffer_files = set()

    while True:
        batch = process_queue.get()

        if batch is None or len(batch) == 0:
            break

        for file in batch:
            data, metadata = prepare_file_data(file, tree_names)

            if metadata and metadata.get("error"):
                try:
                    move_file_to_failed_dir(file["path"], dirs["failed"])
                except Exception:
                    pass
                with counter_lock:
                    stat_fail.value += 1
                    processed.value += 1
                continue

            if data is None:
                with counter_lock:
                    processed.value += 1
                continue

            with counter_lock:
                processed.value += 1
            FILES_INGESTER_COUNTER.labels(ingester=INGESTER_GRAFANA_LABEL).inc()

            commit_enrichments: dict[str, CommitEnrichment] = {}
            if metadata:
                commit_enrichments = metadata.get("commit_enrichments", {})

            instances = build_instances_from_submission(
                data,
                MAP_TABLENAMES_TO_COUNTER,
                commit_enrichments=commit_enrichments,
            )

            instances_dict["commits"].extend(instances["commits"])
            instances_dict["issues"].extend(instances["issues"])
            instances_dict["checkouts"].extend(instances["checkouts"])
            instances_dict["build_definitions"].extend(instances["build_definitions"])
            instances_dict["builds"].extend(instances["builds"])
            instances_dict["build_runs"].extend(instances["build_runs"])
            instances_dict["build_run_payloads"].extend(instances["build_run_payloads"])
            instances_dict["test_definitions"].extend(instances["test_definitions"])
            instances_dict["tests"].extend(instances["tests"])
            instances_dict["test_runs"].extend(instances["test_runs"])
            instances_dict["test_run_payloads"].extend(instances["test_run_payloads"])
            instances_dict["incidents"].extend(instances["incidents"])

            buffer_files.add((file["name"], file["path"]))

        # Sort instances to prevent deadlocks when multiple transactions update the same rows
        instances_dict["commits"].sort(
            key=lambda x: (
                x.tree_name or "",
                x.git_repository_url or "",
                x.git_repository_branch or "",
                x.git_commit_hash,
            )
        )
        instances_dict["issues"].sort(key=lambda x: x.id)
        instances_dict["checkouts"].sort(key=lambda x: x.id)
        instances_dict["build_definitions"].sort(
            key=lambda x: (x.checkout_id, x.series)
        )
        instances_dict["builds"].sort(key=lambda x: x.id)
        instances_dict["build_runs"].sort(key=lambda x: x.id)
        instances_dict["build_run_payloads"].sort(
            key=lambda x: getattr(x, "_legacy_build_id", "") or ""
        )
        instances_dict["test_definitions"].sort(
            key=lambda x: (
                getattr(x, "_legacy_build_id", "") or "",
                x.path or "",
                x.number_prefix or "",
                x.number_unit or "",
            )
        )
        instances_dict["tests"].sort(key=lambda x: x.id)
        instances_dict["test_runs"].sort(key=lambda x: x.id)
        instances_dict["test_run_payloads"].sort(
            key=lambda x: getattr(x, "_legacy_test_id", "") or ""
        )
        instances_dict["incidents"].sort(key=lambda x: x.id)

        should_flush_checkouts = len(instances_dict["checkouts"]) >= INGEST_BATCH_SIZE
        should_flush_commits = (
            should_flush_checkouts
            or len(instances_dict["commits"]) >= INGEST_BATCH_SIZE
        )

        flush_buffers(
            commits_buf=(instances_dict["commits"] if should_flush_commits else []),
            issues_buf=(
                instances_dict["issues"]
                if len(instances_dict["issues"]) >= INGEST_BATCH_SIZE
                else []
            ),
            checkouts_buf=(
                instances_dict["checkouts"] if should_flush_checkouts else []
            ),
            build_definitions_buf=(
                instances_dict["build_definitions"]
                if len(instances_dict["build_definitions"]) >= INGEST_BATCH_SIZE
                else []
            ),
            builds_buf=(
                instances_dict["builds"]
                if len(instances_dict["builds"]) >= INGEST_BATCH_SIZE
                else []
            ),
            build_runs_buf=(
                instances_dict["build_runs"]
                if len(instances_dict["build_runs"]) >= INGEST_BATCH_SIZE
                else []
            ),
            build_run_payloads_buf=(
                instances_dict["build_run_payloads"]
                if len(instances_dict["build_run_payloads"]) >= INGEST_BATCH_SIZE
                else []
            ),
            test_definitions_buf=(
                instances_dict["test_definitions"]
                if len(instances_dict["test_definitions"]) >= INGEST_BATCH_SIZE
                else []
            ),
            tests_buf=(
                instances_dict["tests"]
                if len(instances_dict["tests"]) >= INGEST_BATCH_SIZE
                else []
            ),
            test_runs_buf=(
                instances_dict["test_runs"]
                if len(instances_dict["test_runs"]) >= INGEST_BATCH_SIZE
                else []
            ),
            test_run_payloads_buf=(
                instances_dict["test_run_payloads"]
                if len(instances_dict["test_run_payloads"]) >= INGEST_BATCH_SIZE
                else []
            ),
            incidents_buf=(
                instances_dict["incidents"]
                if len(instances_dict["incidents"]) >= INGEST_BATCH_SIZE
                else []
            ),
            buffer_files=buffer_files,
            dirs=dirs,
            stat_ok=stat_ok,
            stat_fail=stat_fail,
            counter_lock=counter_lock,
        )

    if any(len(instances_dict[table]) for table in instances_dict):
        out("Process finished, flushing remaining buffers")
        flush_buffers(
            commits_buf=instances_dict["commits"],
            issues_buf=instances_dict["issues"],
            checkouts_buf=instances_dict["checkouts"],
            build_definitions_buf=instances_dict["build_definitions"],
            builds_buf=instances_dict["builds"],
            build_runs_buf=instances_dict["build_runs"],
            build_run_payloads_buf=instances_dict["build_run_payloads"],
            test_definitions_buf=instances_dict["test_definitions"],
            tests_buf=instances_dict["tests"],
            test_runs_buf=instances_dict["test_runs"],
            test_run_payloads_buf=instances_dict["test_run_payloads"],
            incidents_buf=instances_dict["incidents"],
            buffer_files=buffer_files,
            dirs=dirs,
            stat_ok=stat_ok,
            stat_fail=stat_fail,
            counter_lock=counter_lock,
        )


def print_ingest_progress(
    processed: int,
    total_files: int,
    total_bytes: int,
    stat_ok: int,
    stat_fail: int,
    elapsed: float,
    queue_size: int,
) -> None:
    """
    Print a report of the ingestion process.
    """
    files_per_sec = total_files / elapsed if elapsed > 0 else 0.0
    mb = total_bytes / (1024 * 1024)
    mb_per_sec = mb / elapsed if elapsed > 0 else 0.0
    rate = processed / elapsed if elapsed > 0 else 0.0
    remaining = total_files - processed
    eta = remaining / rate if rate > 0 else float("inf")

    if remaining > 0:
        msg = (
            "Progress: %d/%d files (ok=%d, fail=%d) | "
            "%.2fs elapsed | %.1f files/s | ETA %.1fs | Queue size: %d"
            % (
                processed,
                total_files,
                stat_ok,
                stat_fail,
                elapsed,
                rate,
                eta,
                queue_size,
            )
        )
    else:
        msg = (
            "Ingest cycle: %d files (ok=%d, fail=%d) in %.2fs | "
            "%.2f files/s | %.2f MB processed (%.2f MB/s)"
            % (
                total_files,
                stat_ok,
                stat_fail,
                elapsed,
                files_per_sec,
                mb,
                mb_per_sec,
            )
        )
    out(msg)


def ingest_submissions_parallel(  # noqa: C901 - orchestrator with IO + multiprocessing
    json_files: list[str],
    tree_names: dict[str, str],
    dirs: dict[INGESTER_DIRS, str],
    max_workers: int = 5,
) -> None:
    """
    Ingest submissions in parallel using child processes for I/O and database operations.
    """
    cycle_start = time.time()
    total_bytes = 0
    total_files_count = len(json_files)

    process_queue: multiprocessing.Queue[Optional[list[SubmissionFileMetadata]]] = (
        multiprocessing.Queue(maxsize=INGEST_QUEUE_MAXSIZE)
    )

    batch = []
    for file_path in json_files:
        try:
            file_size = os.path.getsize(file_path)
        except OSError:
            file_size = 0

        total_bytes += file_size
        batch.append(
            SubmissionFileMetadata(
                path=file_path,
                name=os.path.basename(file_path),
                size=file_size,
            )
        )

        batch_len = len(batch)
        if batch_len >= INGEST_FILES_BATCH_SIZE or batch_len >= total_files_count:
            process_queue.put(batch)
            batch = []

    out(
        "Spool status: %d .json files queued (%.2f MB)"
        % (
            len(json_files),
            total_bytes / (1024 * 1024) if total_bytes else 0.0,
        )
    )

    stat_ok = multiprocessing.Value("i", 0)
    stat_fail = multiprocessing.Value("i", 0)
    counter_lock = multiprocessing.Lock()
    processed = multiprocessing.Value("i", 0)
    last_progress = cycle_start
    progress_every_sec = 2.0

    writers = []
    try:
        for _ in range(max_workers):
            writer = multiprocessing.Process(
                target=process_batch,
                args=(
                    process_queue,
                    tree_names,
                    dirs,
                    processed,
                    stat_ok,
                    stat_fail,
                    counter_lock,
                ),
            )
            writers.append(writer)
            writer.start()
            process_queue.put(None)  # Poison pill to signal the end of the queue

        while not process_queue.empty():
            if time.time() - last_progress > progress_every_sec:
                print_ingest_progress(
                    processed.value,
                    total_files_count,
                    total_bytes,
                    stat_ok.value,
                    stat_fail.value,
                    time.time() - cycle_start,
                    process_queue.qsize(),
                )
                last_progress = time.time()
            time.sleep(1)

        for writer in writers:
            writer.join()
    except KeyboardInterrupt:
        out("\nKeyboardInterrupt: terminating workers...")
        for writer in writers:
            if writer.is_alive():
                writer.terminate()
        for writer in writers:
            writer.join()
        out("Workers terminated.")

    elapsed = time.time() - cycle_start
    total_files = total_files_count
    print_ingest_progress(
        processed.value,
        total_files,
        total_bytes,
        stat_ok.value,
        stat_fail.value,
        elapsed,
        process_queue.qsize(),
    )
