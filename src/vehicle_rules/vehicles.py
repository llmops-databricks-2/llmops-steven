"""Find a plate in a message and look up its car in the RDW tables.

The agent's code runs this, not the model, so that a car's facts never depend on what the model chooses. The
tables are in Delta, so the lookup goes through the SQL warehouse.
"""

import re
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem, StatementState

from vehicle_rules.config import ProjectConfig

VEHICLES_TABLE = "rdw_registered_vehicles"
FUEL_TABLE = "rdw_registered_vehicles_fuel"

# The shapes of every plate in the table, with L for a letter and 9 for a digit. Each mixes the two, which keeps
# ordinary words and numbers out.
PLATE_SHAPES = {
    "L999LL",
    "LL999L",
    "99LLL9",
    "LLL99L",
    "99LLLL",
    "9LLL99",
    "LLLL99",
    "LL9999",
    "LL99LL",
    "99LL99",
    "9999LL",
}
# A plate with its dashes, or without them. Not with spaces: "in 19 am" would look like a plate.
PLATE_CANDIDATE = re.compile(r"(?<![\w.-])(?:\w{1,4}-\w{1,4}-\w{1,4}|\w{6})(?![\w-]|\.\w)")

# The register is in Dutch and the regulation's chunks in English, so the agent gets the regulation's words. A value
# not listed here is the same in both, like Diesel, or passes as it is.
VEHICLE_TYPES_IN_ENGLISH = {"Personenauto": "Passenger cars"}
FUELS_IN_ENGLISH = {"Benzine": "Petrol", "Elektriciteit": "Electricity", "Waterstof": "Hydrogen"}

# What the regulation's conditions can depend on.
CAR_COLUMNS = (
    "license_plate",
    "vehicle_type",
    "make",
    "trade_name",
    "first_admission_date",
    "first_registration_date_nl",
    "mot_expiry_date",
    "european_vehicle_category",
    "number_of_seats",
    "number_of_doors",
    "unladen_mass",
    "kerb_weight",
    "permitted_maximum_mass",
    "max_design_speed",
)


def normalize_plate(text: str) -> str | None:
    """Return a plate in the form the table holds, six uppercase letters and digits; None if it is not a plate."""
    plate = text.replace("-", "").upper()
    return plate if _shape(plate) in PLATE_SHAPES else None


def _shape(plate: str) -> str:
    """Return a plate's shape: "AB123C" gives "LL999L"."""
    return re.sub(r"[0-9]", "9", re.sub(r"[A-Z]", "L", plate))


def find_plate(message: str) -> str | None:
    """Return the first plate in a message, normalized; None if it has none."""
    return next(filter(None, map(normalize_plate, PLATE_CANDIDATE.findall(message))), None)


def lookup_car(config: ProjectConfig, workspace: WorkspaceClient, plate: str) -> dict[str, Any] | None:
    """Return a car's facts in English, with its fuels; None if the plate is not in the table."""
    columns = ", ".join(CAR_COLUMNS)
    cars = _query(config, workspace, f"SELECT {columns} FROM {{}}.{VEHICLES_TABLE} WHERE license_plate = :plate", plate)
    if not cars:
        return None
    fuels = _query(
        config,
        workspace,
        f"SELECT fuel_description FROM {{}}.{FUEL_TABLE} WHERE license_plate = :plate ORDER BY fuel_sequence_number",
        plate,
    )
    car = cars[0]
    return car | {
        "vehicle_type": VEHICLE_TYPES_IN_ENGLISH.get(car["vehicle_type"], car["vehicle_type"]),
        "fuels": [FUELS_IN_ENGLISH.get(f["fuel_description"], f["fuel_description"]) for f in fuels],
    }


def _query(config: ProjectConfig, workspace: WorkspaceClient, statement: str, plate: str) -> list[dict[str, Any]]:
    """Run a statement on a plate on the warehouse; return its rows as dictionaries, with values as strings."""
    response = workspace.statement_execution.execute_statement(
        warehouse_id=config.warehouse_id,
        statement=statement.format(config.full_schema_name),
        parameters=[StatementParameterListItem(name="plate", value=plate)],
        wait_timeout="50s",
    )
    if response.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"The plate lookup ended in {response.status.state}: {response.status.error}")
    names = [column.name for column in response.manifest.schema.columns]
    return [dict(zip(names, row, strict=True)) for row in response.result.data_array or []]
