from typing import Literal, Type

from kernelCI_app.models import (
    BuildDefinitions,
    BuildRunPayloads,
    BuildRuns,
    Builds,
    Checkouts,
    Commits,
    Hardwares,
    Incidents,
    Issues,
    TestDefinitions,
    TestRunHardwares,
    TestRunPayloads,
    TestRuns,
    Tests,
)

type TableNames = Literal[
    "commits",
    "issues",
    "checkouts",
    "build_definitions",
    "builds",
    "build_runs",
    "build_run_payloads",
    "hardwares",
    "test_definitions",
    "tests",
    "test_runs",
    "test_run_hardwares",
    "test_run_payloads",
    "incidents",
]
type TableModels = (
    Commits
    | Issues
    | Checkouts
    | BuildDefinitions
    | Builds
    | BuildRuns
    | BuildRunPayloads
    | Hardwares
    | TestDefinitions
    | Tests
    | TestRuns
    | TestRunHardwares
    | TestRunPayloads
    | Incidents
)
type TableModelsClass = (
    Type[Commits]
    | Type[Issues]
    | Type[Checkouts]
    | Type[BuildDefinitions]
    | Type[Builds]
    | Type[BuildRuns]
    | Type[BuildRunPayloads]
    | Type[Hardwares]
    | Type[TestDefinitions]
    | Type[Tests]
    | Type[TestRuns]
    | Type[TestRunHardwares]
    | Type[TestRunPayloads]
    | Type[Incidents]
)

MODEL_MAP: dict[TableNames, TableModelsClass] = {
    "commits": Commits,
    "issues": Issues,
    "checkouts": Checkouts,
    "build_definitions": BuildDefinitions,
    "builds": Builds,
    "build_runs": BuildRuns,
    "build_run_payloads": BuildRunPayloads,
    "hardwares": Hardwares,
    "test_definitions": TestDefinitions,
    "tests": Tests,
    "test_runs": TestRuns,
    "test_run_hardwares": TestRunHardwares,
    "test_run_payloads": TestRunPayloads,
    "incidents": Incidents,
}
