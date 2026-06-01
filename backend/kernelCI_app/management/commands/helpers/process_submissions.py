import logging
from typing import Any, TypedDict

from django.conf import settings
from django.db import IntegrityError
from django.utils import timezone
from prometheus_client import Counter
from pydantic import ValidationError

from kernelCI_app.constants.ingester import INGESTER_GRAFANA_LABEL
from kernelCI_app.models import (
    BuildDefinitions,
    BuildRuns,
    Builds,
    Checkouts,
    Commits,
    Incidents,
    Issues,
    TestDefinitions,
    TestRuns,
    Tests,
)
from kernelCI_app.typeModels.modelTypes import TableNames


class ProcessedSubmission(TypedDict):
    """Stores the list of items in a single submission.
    Lists can't be None but can be empty."""

    commits: list[Commits]
    issues: list[Issues]
    checkouts: list[Checkouts]
    build_definitions: list[BuildDefinitions]
    builds: list[Builds]
    build_runs: list[BuildRuns]
    test_definitions: list[TestDefinitions]
    tests: list[Tests]
    test_runs: list[TestRuns]
    incidents: list[Incidents]


logger = logging.getLogger(__name__)


def get_model_fields(model_fields) -> set[str]:
    """
    Gathers a set of the field_names of a model in order to validate them.
    This is done such that django doesn't complain about extra fields in a model.
    """
    valid_fields = set()
    for field in model_fields:
        if field.__class__.__name__ == "ForeignKey":
            valid_fields.add(f"{field.name}_id")
        else:
            valid_fields.add(field.name)
    return valid_fields


ISSUE_FIELDS = get_model_fields(Issues._meta.get_fields())
CHECKOUT_FIELDS = get_model_fields(Checkouts._meta.get_fields())
COMMIT_FIELDS = get_model_fields(Commits._meta.get_fields()) - {"id"}
BUILD_DEFINITION_FIELDS = get_model_fields(BuildDefinitions._meta.get_fields()) - {"id"}
BUILD_FIELDS = get_model_fields(Builds._meta.get_fields())
BUILD_RUN_FIELDS = get_model_fields(BuildRuns._meta.get_fields()) - {"id"}
TEST_DEFINITION_FIELDS = get_model_fields(TestDefinitions._meta.get_fields()) - {"id"}
TEST_FIELDS = get_model_fields(Tests._meta.get_fields())
TEST_RUN_FIELDS = get_model_fields(TestRuns._meta.get_fields()) - {"id"}
INCIDENT_FIELDS = get_model_fields(Incidents._meta.get_fields())


def flatten_dict_specific(target: dict[str, Any], target_fields: list[str]):
    """
    Flatten specific fields of a dict on a one-level-deep only.
    Done in order to avoid flattening fields that should be kept as JSONs
     (which happens with other libraries such as flatdict).

    Example of `flatten_dict_specific(test_data, ["environment"])`:
    `test_data` will go from
    ```
    {
        environment: {
            comment: "foo"
            misc: {
                platform: "bar"
            }
        }
    }
    ```
    to
    ```
    {
        environment_comment: "foo"
        environment_misc: {
            platform: "bar"
        }
    }
    """

    separator = "_"

    flattened_dict = target.copy()
    for first_key, value in target.items():
        if first_key in target_fields:
            if isinstance(value, dict):
                for (
                    inner_key,
                    real_value,
                ) in value.items():
                    merged_key = separator.join([first_key, inner_key])
                    flattened_dict[merged_key] = real_value
                del flattened_dict[first_key]
            else:
                print(f"Target key {first_key} is not a dict")
                continue

    return flattened_dict


def make_issue_instance(issue: dict[str, Any]) -> Issues:
    flattened_issue = flatten_dict_specific(issue, ["culprit"])
    filtered_issue = {
        key: value for key, value in flattened_issue.items() if key in ISSUE_FIELDS
    }
    obj = Issues(**filtered_issue)
    obj.field_timestamp = timezone.now()
    return obj


def make_checkout_instance(
    checkout: dict[str, Any], commit_enrichment: dict[str, Any] | None = None
) -> Checkouts:
    enriched_checkout = checkout | (commit_enrichment or {})
    filtered_checkout = {
        key: value for key, value in enriched_checkout.items() if key in CHECKOUT_FIELDS
    }
    obj = Checkouts(**filtered_checkout)
    obj.field_timestamp = timezone.now()
    return obj


def make_commit_instance_from_checkout(
    checkout: dict[str, Any], commit_enrichment: dict[str, Any] | None = None
) -> Commits | None:
    if not checkout.get("git_commit_hash"):
        return None

    enriched_checkout = checkout | (commit_enrichment or {})
    filtered_commit = {
        key: value for key, value in enriched_checkout.items() if key in COMMIT_FIELDS
    }
    obj = Commits(**filtered_commit)
    obj.field_timestamp = timezone.now()
    return obj


def make_build_instance(build: dict[str, Any]) -> Builds:
    filtered_build = {key: value for key, value in build.items() if key in BUILD_FIELDS}
    obj = Builds(**filtered_build)
    obj.field_timestamp = timezone.now()
    return obj


def make_build_definition_instance_from_build(
    build: dict[str, Any],
) -> BuildDefinitions:
    filtered_build_definition = {
        key: value for key, value in build.items() if key in BUILD_DEFINITION_FIELDS
    }
    obj = BuildDefinitions(**filtered_build_definition)
    obj.field_timestamp = timezone.now()
    return obj


def make_build_run_instance_from_build(build: dict[str, Any]) -> BuildRuns:
    build_run_data = build | {"kci_id": build.get("id")}
    filtered_build_run = {
        key: value for key, value in build_run_data.items() if key in BUILD_RUN_FIELDS
    }
    obj = BuildRuns(**filtered_build_run)
    obj._definition_series = build.get("series")
    obj.field_timestamp = timezone.now()
    return obj


def make_test_instance(test: dict[str, Any]) -> Tests:
    flattened_test = flatten_dict_specific(test, ["environment", "number"])
    filtered_test = {
        key: value for key, value in flattened_test.items() if key in TEST_FIELDS
    }
    obj = Tests(**filtered_test)
    obj.field_timestamp = timezone.now()
    return obj


def make_test_definition_instance_from_test(test: dict[str, Any]) -> TestDefinitions:
    flattened_test = flatten_dict_specific(test, ["environment", "number"])
    filtered_test_definition = {
        key: value
        for key, value in flattened_test.items()
        if key in TEST_DEFINITION_FIELDS
    }
    obj = TestDefinitions(**filtered_test_definition)
    obj._legacy_build_id = flattened_test.get("build_id")
    obj.field_timestamp = timezone.now()
    return obj


def make_test_run_instance_from_test(test: dict[str, Any]) -> TestRuns:
    flattened_test = flatten_dict_specific(test, ["environment", "number"])
    path = flattened_test.get("path")
    environment_misc = flattened_test.get("environment_misc") or {}
    is_boot = None
    if path is not None:
        is_boot = path == "boot" or path.startswith("boot.")
    test_run_data = flattened_test | {
        "kci_id": flattened_test.get("id"),
        "is_boot": is_boot,
        "platform": environment_misc.get("platform"),
    }
    filtered_test_run = {
        key: value for key, value in test_run_data.items() if key in TEST_RUN_FIELDS
    }
    obj = TestRuns(**filtered_test_run)
    obj._legacy_build_id = flattened_test.get("build_id")
    obj._definition_path = flattened_test.get("path")
    obj._definition_number_prefix = flattened_test.get("number_prefix")
    obj._definition_number_unit = flattened_test.get("number_unit")
    obj.field_timestamp = timezone.now()
    return obj


def make_incident_instance(incident: dict[str, Any]) -> Incidents:
    filtered_incident = {
        key: value for key, value in incident.items() if key in INCIDENT_FIELDS
    }
    obj = Incidents(**filtered_incident)
    obj.field_timestamp = timezone.now()
    return obj


def build_instances_from_submission(
    data: dict[str, Any],
    counters: dict[TableNames, Counter],
    commit_enrichments: dict[str, dict[str, Any]] | None = None,
) -> ProcessedSubmission:
    """
    Convert raw submission dicts into unsaved Django model instances, grouped by type.
    Per-item errors are logged and the item is skipped, matching the previous behavior.

    Params:
        data: the submission data in dict format
        counters: a dict mapping tables to its prometheus counter
    """
    out: ProcessedSubmission = {
        "commits": [],
        "issues": [],
        "checkouts": [],
        "build_definitions": [],
        "builds": [],
        "build_runs": [],
        "test_definitions": [],
        "tests": [],
        "test_runs": [],
        "incidents": [],
    }
    if commit_enrichments is None:
        commit_enrichments = {}
    should_dual_write_runs = settings.DB_SCHEMA_REFACTOR_DUAL_WRITE

    def _process(items, item_type: TableNames):
        if not items:
            return
        for item in items:
            if not isinstance(item, dict):
                logger.warning(
                    f"{item_type.capitalize()} data is not a dict, its type is: {type(item)}"
                )
                continue
            try:
                match item_type:
                    case "issues":
                        issue = make_issue_instance(item)
                        out["issues"].append(issue)
                        counters["issues"].labels(
                            ingester=INGESTER_GRAFANA_LABEL, origin=issue.origin
                        ).inc()
                    case "checkouts":
                        checkout_enrichment = commit_enrichments.get(item.get("id"), {})
                        commit = make_commit_instance_from_checkout(
                            item, checkout_enrichment
                        )
                        if commit is not None:
                            out["commits"].append(commit)

                        checkout = make_checkout_instance(item, checkout_enrichment)
                        out["checkouts"].append(checkout)
                        counters["checkouts"].labels(
                            ingester=INGESTER_GRAFANA_LABEL, origin=checkout.origin
                        ).inc()
                    case "builds":
                        build = make_build_instance(item)
                        out["builds"].append(build)

                        if should_dual_write_runs:
                            build_definition = (
                                make_build_definition_instance_from_build(item)
                            )
                            out["build_definitions"].append(build_definition)

                            build_run = make_build_run_instance_from_build(item)
                            out["build_runs"].append(build_run)

                        try:
                            misc = build.misc
                            lab = misc.get("lab")
                        except AttributeError:
                            lab = None

                        counters["builds"].labels(
                            ingester=INGESTER_GRAFANA_LABEL,
                            origin=build.origin,
                            lab=lab,
                        ).inc()
                    case "tests":
                        test = make_test_instance(item)
                        out["tests"].append(test)

                        if should_dual_write_runs:
                            test_definition = make_test_definition_instance_from_test(
                                item
                            )
                            out["test_definitions"].append(test_definition)

                            test_run = make_test_run_instance_from_test(item)
                            out["test_runs"].append(test_run)

                        try:
                            misc = test.misc
                            lab = misc.get("lab", misc.get("runtime"))
                        except AttributeError:
                            lab = None

                        try:
                            environment_misc = test.environment_misc
                            platform = environment_misc.get("platform")
                        except AttributeError:
                            platform = None

                        counters["tests"].labels(
                            ingester=INGESTER_GRAFANA_LABEL,
                            origin=test.origin,
                            lab=lab,
                            platform=platform,
                        ).inc()
                    case "incidents":
                        incident = make_incident_instance(item)
                        out["incidents"].append(incident)
                        counters["incidents"].labels(
                            ingester=INGESTER_GRAFANA_LABEL, origin=incident.origin
                        ).inc()
                    case _:
                        raise ValueError(f"Unknown item type: {item_type}")
            except ValidationError as ve:
                logger.error(f"Validation error for {item_type} item: {ve}")
                continue
            except Exception as e:
                logger.error(f"{e.__class__.__name__} error for {item_type} item: {e}")
                continue

    _process(data.get("issues"), "issues")
    _process(data.get("checkouts"), "checkouts")
    _process(data.get("builds"), "builds")
    _process(data.get("tests"), "tests")
    _process(data.get("incidents"), "incidents")

    return out


def insert_items(
    item_type: TableNames,
    items: list[dict[str, Any]],
):
    logger.info(f"Processing {len(items)} {item_type}")
    item_counter = 0
    success_counter = 0

    for item in items:
        if not isinstance(item, dict):
            logger.warning(
                f"{item_type.capitalize()} data is not a dict, its type is: {type(item)}"
            )
            continue
        item_counter += 1

        try:
            match item_type:
                case "issues":
                    model_instance = make_issue_instance(item)
                case "checkouts":
                    model_instance = make_checkout_instance(item)
                case "builds":
                    model_instance = make_build_instance(item)
                case "tests":
                    model_instance = make_test_instance(item)
                case "incidents":
                    model_instance = make_incident_instance(item)
                case _:
                    raise ValueError(f"Unknown item type: {item_type}")

            model_instance.save()
            success_counter += 1
        except ValidationError as ve:
            logger.error(f"Validation error for {item_type} item: {ve}")
            continue
        # TODO: catch whenever there is a problem with the schema and deal with it
        except IntegrityError as ie:
            logger.error(f"Integrity error for {item_type} item: {ie}")
            continue
        except Exception as e:
            logger.error(f"{e.__class__.__name__} error for {item_type} item: {e}")
            continue

    print(f"Processed {item_counter} {item_type}, {success_counter} succeeded")
    return success_counter


def insert_submission_data(data: dict[str, Any], metadata: dict[str, Any]):
    """
    Processes the data from a submission file.
    """
    logger.info(
        "Processing submission data for %s", metadata.get("filename", "unknown")
    )

    try:
        # Note that the order of processing is important, as some data depends on others
        # Checkouts > Builds > Tests
        # Issues && Builds && Tests > Incidents
        if issues := data.get("issues"):
            insert_items("issues", issues)
        if checkouts := data.get("checkouts"):
            insert_items("checkouts", checkouts)
        if builds := data.get("builds"):
            insert_items("builds", builds)
        if tests := data.get("tests"):
            insert_items("tests", tests)
        if incidents := data.get("incidents"):
            insert_items("incidents", incidents)
    except Exception as e:
        logger.error(f"Error processing submission data: {e}")
        raise e

    logger.info(
        "Successfully parsed %s submission file", metadata.get("filename", "unknown")
    )
