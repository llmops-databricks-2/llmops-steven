"""Search the chunks in Postgres: by meaning, or by meaning and keywords together.

Both searches read the synced table, a copy of regulation_chunks, so nothing here writes. They are exact: with
no vector index they score every chunk of a granularity, which is fast enough at about 5,000 chunks and means
that a filter cannot lose results, as it can behind an approximate index.

A search takes the question's embedding rather than making it, so that comparing searches on one question costs
one call to the embedding model.

Only the chapter 5 articles of a vehicle type have a `vehicle_type`. The rest, the annexes among them, apply to
any vehicle, so filtering on a vehicle type keeps them and leaves out the articles of other vehicle types.

Keyword matching is on `text`, the English that was embedded, which starts with the chunk's reference.
"""

from typing import Any

import psycopg
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.postgres import EndpointType
from psycopg import sql
from psycopg.rows import dict_row

from vehicle_rules.config import ProjectConfig

DEFAULT_LIMIT = 10
# The vector score's share of a hybrid score: the lecture's 70/30, not tuned.
VECTOR_WEIGHT = 0.7

_SCORED_CHUNKS = """
    SELECT id, annex, article, paragraph, vehicle_type, text,
           1 - (embedding <=> %(embedding)s::vector) AS vector_score
    FROM {table}
    WHERE granularity = %(granularity)s
      AND (%(vehicle_type)s::text IS NULL OR vehicle_type = %(vehicle_type)s OR vehicle_type IS NULL)
"""

_SEMANTIC = f"""
    {_SCORED_CHUNKS}
    ORDER BY vector_score DESC, id
    LIMIT %(limit)s
"""

# plainto_tsquery needs every word of the question to match, which a whole sentence rarely does, so its "&" becomes
# "|", any word. ts_rank_cd has no upper bound: its normalization 32, rank / (rank + 1), brings it to 0-1 like the
# vector score, so that the weight means what it says.
_HYBRID = f"""
    WITH chunks AS ({_SCORED_CHUNKS}),
    ranked AS (
        SELECT *, ts_rank_cd(
            to_tsvector('english', text),
            replace(plainto_tsquery('english', %(question)s)::text, '&', '|')::tsquery,
            32
        ) AS text_score
        FROM chunks
    )
    SELECT *, %(vector_weight)s * vector_score + (1 - %(vector_weight)s) * text_score AS score
    FROM ranked
    ORDER BY score DESC, id
    LIMIT %(limit)s
"""


def connect(config: ProjectConfig, workspace: WorkspaceClient) -> psycopg.Connection:
    """Connect to the project's database as the current identity, with a short-lived credential."""
    branch = f"projects/{config.lakebase_instance}/branches/production"
    endpoint = next(
        e
        for e in workspace.postgres.list_endpoints(parent=branch)
        if e.status.endpoint_type == EndpointType.ENDPOINT_TYPE_READ_WRITE
    )
    return psycopg.connect(
        host=endpoint.status.hosts.host,
        dbname=config.lakebase_database,
        user=workspace.current_user.me().user_name,
        password=workspace.postgres.generate_database_credential(endpoint=endpoint.name).token,
        sslmode="require",
        autocommit=True,
    )


def embed_question(config: ProjectConfig, workspace: WorkspaceClient, question: str) -> list[float]:
    """Embed a question with the model that embedded the chunks."""
    client = workspace.serving_endpoints.get_open_ai_client()
    return client.embeddings.create(model=config.embedding_endpoint, input=question).data[0].embedding


def semantic(
    conn: psycopg.Connection,
    config: ProjectConfig,
    embedding: list[float],
    granularity: str,
    vehicle_type: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Return the chunks of a granularity closest in meaning to the question, best first, with `vector_score`."""
    params = {"embedding": str(embedding), "granularity": granularity, "vehicle_type": vehicle_type, "limit": limit}
    return _fetch(conn, config, _SEMANTIC, params)


def hybrid(
    conn: psycopg.Connection,
    config: ProjectConfig,
    question: str,
    embedding: list[float],
    granularity: str,
    vehicle_type: str | None = None,
    vector_weight: float = VECTOR_WEIGHT,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Return the chunks of a granularity best by meaning and keywords, with `vector_score`, `text_score`, `score`."""
    params = {
        "question": question,
        "embedding": str(embedding),
        "granularity": granularity,
        "vehicle_type": vehicle_type,
        "vector_weight": vector_weight,
        "limit": limit,
    }
    return _fetch(conn, config, _HYBRID, params)


def _fetch(conn: psycopg.Connection, config: ProjectConfig, query: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Run a search on the synced table, in the Postgres schema named after the Unity Catalog one."""
    table = sql.Identifier(config.db_schema, config.synced_table)
    with conn.cursor(row_factory=dict_row) as cursor:
        return cursor.execute(sql.SQL(query).format(table=table), params).fetchall()
