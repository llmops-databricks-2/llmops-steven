"""The statements that set the table comments, built without a workspace."""

from unittest.mock import MagicMock

from vehicle_rules.table_comments import COLUMN_COMMENTS, TABLE_COMMENTS, _literal, apply, statements


def test_every_comment_gets_a_statement_in_the_schema() -> None:
    """One statement per table and per column, each in the given schema."""
    result = statements("llmops_dev.steven")
    assert len(result) == len(TABLE_COMMENTS) + sum(map(len, COLUMN_COMMENTS.values()))
    assert all("llmops_dev.steven.rdw_registered_vehicles" in statement for statement in result)


def test_a_comment_cannot_end_its_own_string() -> None:
    """A quote in a comment is escaped."""
    assert _literal("the car's date") == r"'the car\'s date'"


def test_apply_runs_every_statement() -> None:
    """Nothing but the statements runs."""
    spark = MagicMock()
    apply(spark, "llmops_dev.steven")
    assert [call.args[0] for call in spark.sql.call_args_list] == statements("llmops_dev.steven")
