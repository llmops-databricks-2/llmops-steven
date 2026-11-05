"""Sessions on a fake connection. A real database is exercised by notebooks/week_3_agent.py."""

from unittest.mock import MagicMock

import pytest

from vehicle_rules.agent import Turn
from vehicle_rules.memory import chat, load_messages, new_session_id, save_messages

MESSAGE = {"role": "user", "content": "Hello"}


def connection(owner: str | None = None, rows: list | None = None) -> MagicMock:
    """Return a connection whose session has the given owner and stored messages."""
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (owner,) if owner else None
    conn.execute.return_value.fetchall.return_value = rows or []
    return conn


def test_session_ids_differ() -> None:
    """They are random, so one cannot be guessed from another."""
    assert new_session_id() != new_session_id()


def test_loading_checks_the_owner_and_keeps_the_order() -> None:
    """Both the session and the owner are in the query, and `id` orders it: a timestamp can tie."""
    conn = connection(rows=[(MESSAGE,)])
    assert load_messages(conn, "s", "ann") == [MESSAGE]
    query, params = conn.execute.call_args.args
    assert params == ("s", "ann")
    assert "owner_id = %s" in query
    assert "ORDER BY id" in query


def test_saving_to_a_session_of_another_owner_fails_and_writes_nothing() -> None:
    """A stolen session ID cannot add to a conversation."""
    conn = connection(owner="ann")
    with pytest.raises(PermissionError):
        save_messages(conn, "s", "bob", [MESSAGE])
    conn.cursor.assert_not_called()


@pytest.mark.parametrize("owner", [None, "ann"])
def test_a_new_session_or_your_own_can_be_saved_to(owner: str | None) -> None:
    """Each message is a row, in one transaction."""
    conn = connection(owner=owner)
    save_messages(conn, "s", "ann", [MESSAGE, MESSAGE])
    conn.transaction.assert_called_once()
    rows = conn.cursor.return_value.__enter__.return_value.executemany.call_args.args[1]
    assert [row[:2] for row in rows] == [("s", "ann"), ("s", "ann")]


def test_a_turn_starts_from_the_history_and_saves_only_what_is_new() -> None:
    """Saving everything again would duplicate the history."""
    conn = connection(rows=[(MESSAGE,)])
    agent = MagicMock()
    agent.chat.return_value = Turn("Hi", [{"role": "assistant", "content": "Hi"}])
    assert chat(conn, agent, "s", "ann", "Again").answer == "Hi"
    agent.chat.assert_called_once_with("Again", [MESSAGE])
    rows = conn.cursor.return_value.__enter__.return_value.executemany.call_args.args[1]
    assert len(rows) == 1
