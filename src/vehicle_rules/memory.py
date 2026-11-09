"""Keep a conversation in Lakebase, so that a turn can continue from the turns before it.

A session ID says which conversation this is, not who may read it, so every load and save also checks the owner.
Each session gets a random ID, and belongs to the owner who saved to it first. For now everything runs as the
current user, so tests use made-up owners. With deployment `owner_id` must come from the verified login, never from
the request.
"""

from typing import Any
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from vehicle_rules.agent import Message, Turn, VehicleRulesAgent

_CREATE = (
    """
    CREATE TABLE IF NOT EXISTS session_messages (
        id BIGSERIAL PRIMARY KEY,
        session_id TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        message_data JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    "CREATE INDEX IF NOT EXISTS session_messages_session ON session_messages (session_id, id)",
)
_OWNER = "SELECT owner_id FROM session_messages WHERE session_id = %s LIMIT 1"
_LOAD = "SELECT message_data FROM session_messages WHERE session_id = %s AND owner_id = %s ORDER BY id"
_SAVE = "INSERT INTO session_messages (session_id, owner_id, message_data) VALUES (%s, %s, %s)"


def new_session_id() -> str:
    """Return a session ID that cannot be guessed."""
    return str(uuid4())


def ensure_table(conn: psycopg.Connection) -> None:
    """Create the table of messages if it is missing."""
    for statement in _CREATE:
        conn.execute(statement)


def load_messages(conn: psycopg.Connection, session_id: str, owner_id: str) -> list[Message]:
    """Return a session's messages in order: none for a new session, or a session of another owner."""
    return [row[0] for row in conn.execute(_LOAD, (session_id, owner_id)).fetchall()]


def save_messages(conn: psycopg.Connection, session_id: str, owner_id: str, messages: list[Message]) -> None:
    """Append new messages to a session, all or none. Fail if the session belongs to another owner."""
    if (row := conn.execute(_OWNER, (session_id,)).fetchone()) and row[0] != owner_id:
        raise PermissionError("This session belongs to another owner.")
    rows: list[tuple[Any, ...]] = [(session_id, owner_id, Jsonb(message)) for message in messages]
    with conn.transaction(), conn.cursor() as cursor:
        cursor.executemany(_SAVE, rows)


def chat(conn: psycopg.Connection, agent: VehicleRulesAgent, session_id: str, owner_id: str, message: str) -> Turn:
    """Answer a message in its session: load the history, run the turn, save what is new."""
    turn = agent.chat(message, load_messages(conn, session_id, owner_id))
    save_messages(conn, session_id, owner_id, turn.messages)
    return turn
