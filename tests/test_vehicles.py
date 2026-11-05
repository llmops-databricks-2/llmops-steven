"""Finding a plate in a message, and looking up its car, without a workspace."""

from unittest.mock import MagicMock

import pytest
from databricks.sdk.service.sql import StatementState

from vehicle_rules.vehicles import find_plate, lookup_car, normalize_plate


@pytest.mark.parametrize(
    "plate",
    ["AB123C", "A123BC", "12ABC3", "ABC12D", "12ABCD", "1ABC23", "ABCD12", "AB1234", "AB12CD", "12AB34", "1234AB"],
)
def test_every_shape_of_plate_is_a_plate(plate: str) -> None:
    """The eleven shapes in the table."""
    assert normalize_plate(plate) == plate


@pytest.mark.parametrize(
    ("message", "plate"),
    [
        ("My plate is AB-123-C, does it need an APK?", "AB123C"),
        ("Check ab123c please", "AB123C"),
        ("AB123C", "AB123C"),
        ("Is 12-ABC-3 ok?", "12ABC3"),
        ("What about (AB-123-C)?", "AB123C"),
    ],
)
def test_a_plate_is_found_in_a_message(message: str, plate: str) -> None:
    """With or without dashes, in any case, within a sentence."""
    assert find_plate(message) == plate


@pytest.mark.parametrize(
    "message",
    [
        "Does my car need seat belts in the back?",
        "What does article 5.2.47 require?",
        "My car is from 1987",
        "Registered on 2026-10-11, or 10-11-2026",
        "A Euro-6-diesel with a 150000 km odometer",
        "It meets Annex VIII, Article 45e",
        "in 19 am",
    ],
)
def test_ordinary_text_is_not_a_plate(message: str) -> None:
    """Words, numbers, dates and article numbers."""
    assert find_plate(message) is None


def workspace(*rows: list[list[str]]) -> MagicMock:
    """Return a workspace whose statements return the given rows, in turn, for the columns a and b."""
    responses = []
    for data in rows:
        response = MagicMock()
        response.status.state = StatementState.SUCCEEDED
        response.manifest.schema.columns = [MagicMock(), MagicMock()]
        response.manifest.schema.columns[0].name = "vehicle_type"
        response.manifest.schema.columns[1].name = "make"
        response.result.data_array = data
        responses.append(response)
    client = MagicMock()
    client.statement_execution.execute_statement.side_effect = responses
    return client


def test_an_unknown_plate_is_not_found() -> None:
    """No row means no car, and no second query."""
    client = workspace([])
    assert lookup_car(MagicMock(full_schema_name="llmops_dev.steven"), client, "AB123C") is None
    assert client.statement_execution.execute_statement.call_count == 1


def test_a_car_comes_with_its_fuels_and_its_vehicle_type_in_the_regulation() -> None:
    """The plate is a bound parameter, and the RDW's Dutch type maps to the regulation's."""
    client = workspace([["Personenauto", "VOLVO"]], [["Benzine", "6"]])
    car = lookup_car(MagicMock(full_schema_name="llmops_dev.steven"), client, "AB123C")
    assert car["regulation_vehicle_type"] == "Passenger cars"
    assert car["fuels"] == [{"vehicle_type": "Benzine", "make": "6"}]
    first_call = client.statement_execution.execute_statement.call_args_list[0].kwargs
    assert "AB123C" not in first_call["statement"]
    assert first_call["parameters"][0].value == "AB123C"


def test_a_failed_statement_raises() -> None:
    """A warehouse that fails must not look like an unknown plate."""
    client = workspace([])
    client.statement_execution.execute_statement.side_effect = None
    client.statement_execution.execute_statement.return_value.status.state = StatementState.FAILED
    with pytest.raises(RuntimeError):
        lookup_car(MagicMock(full_schema_name="llmops_dev.steven"), client, "AB123C")
