from typing import Literal, Type

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

type TableNames = Literal[
    "commits",
    "issues",
    "checkouts",
    "build_definitions",
    "builds",
    "build_runs",
    "test_definitions",
    "tests",
    "test_runs",
    "incidents",
]
type TableModels = (
    Commits
    | Issues
    | Checkouts
    | BuildDefinitions
    | Builds
    | BuildRuns
    | TestDefinitions
    | Tests
    | TestRuns
    | Incidents
)
type TableModelsClass = (
    Type[Commits]
    | Type[Issues]
    | Type[Checkouts]
    | Type[BuildDefinitions]
    | Type[Builds]
    | Type[BuildRuns]
    | Type[TestDefinitions]
    | Type[Tests]
    | Type[TestRuns]
    | Type[Incidents]
)

MODEL_MAP: dict[TableNames, TableModelsClass] = {
    "commits": Commits,
    "issues": Issues,
    "checkouts": Checkouts,
    "build_definitions": BuildDefinitions,
    "builds": Builds,
    "build_runs": BuildRuns,
    "test_definitions": TestDefinitions,
    "tests": Tests,
    "test_runs": TestRuns,
    "incidents": Incidents,
}
