from typing import Literal, Type

from kernelCI_app.models import Builds, Checkouts, Commits, Incidents, Issues, Tests

type TableNames = Literal[
    "commits", "issues", "checkouts", "builds", "tests", "incidents"
]
type TableModels = Commits | Issues | Checkouts | Builds | Tests | Incidents
type TableModelsClass = (
    Type[Commits]
    | Type[Issues]
    | Type[Checkouts]
    | Type[Builds]
    | Type[Tests]
    | Type[Incidents]
)

MODEL_MAP: dict[TableNames, TableModelsClass] = {
    "commits": Commits,
    "issues": Issues,
    "checkouts": Checkouts,
    "builds": Builds,
    "tests": Tests,
    "incidents": Incidents,
}
