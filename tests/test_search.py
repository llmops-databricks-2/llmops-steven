"""The searches' SQL, built without a database: running it is left to notebooks/week_2_search.py."""

import re
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from vehicle_rules.config import ProjectConfig
from vehicle_rules.search import hybrid, semantic

CONFIG = MagicMock(spec=ProjectConfig, db_schema="steven", synced_table="regulation_chunks_synced")
EMBEDDING = [0.25, 0.5]


def run(search: Callable[..., list[dict[str, Any]]], *args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:  # noqa: ANN401
    """Run a search on a fake connection; return the query and parameters it sent."""
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    search(conn, CONFIG, *args, **kwargs)
    query, params = cursor.execute.call_args.args
    return query.as_string(), params


@pytest.mark.parametrize(
    ("search", "args"),
    [(semantic, (EMBEDDING, "article")), (hybrid, ("Tinted windows?", EMBEDDING, "article"))],
)
def test_a_search_reads_the_synced_table_and_supplies_every_parameter(search: Callable, args: tuple) -> None:
    """The table is filled in, and the query's named parameters are exactly those sent."""
    query, params = run(search, *args)
    assert '"steven"."regulation_chunks_synced"' in query
    assert set(re.findall(r"%\((\w+)\)s", query)) == set(params)


def test_the_embedding_is_sent_in_pgvector_text_form() -> None:
    """Postgres casts "[0.25, 0.5]" to a vector; psycopg would otherwise send an array."""
    _, params = run(semantic, EMBEDDING, "paragraph")
    assert params["embedding"] == "[0.25, 0.5]"


def test_the_filter_keeps_chunks_without_a_vehicle_type() -> None:
    """The annexes have no vehicle type, and a filter must not lose them."""
    query, params = run(semantic, EMBEDDING, "article", vehicle_type="Passenger cars")
    assert params["vehicle_type"] == "Passenger cars"
    assert "vehicle_type IS NULL" in query


def test_hybrid_matches_any_word_of_the_question() -> None:
    """A whole sentence rarely matches with every word required."""
    query, _ = run(hybrid, "Tinted windows?", EMBEDDING, "article")
    assert "'&', '|'" in query
