# Databricks notebook source
# /// script
# dependencies = [
#   "..",
# ]
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # Week 2: the chunks in Lakebase
# MAGIC
# MAGIC Homework item 03: mirror `regulation_chunks` into the Lakebase project as a synced table, and confirm its
# MAGIC primary key, vector type, synchronization state and row count.
# MAGIC
# MAGIC The course offers AI Search or Lakebase and recommends Lakebase: an AI Search endpoint bills while it
# MAGIC exists, with or without queries, while the Lakebase compute scales to zero when idle. The synced table
# MAGIC is triggered, not continuous, so each sync is one pipeline run that then stops. Delta stays the source
# MAGIC of truth: change `regulation_chunks` (the `chunks` task of the `ingest` job) and sync again, never
# MAGIC write to the copy in Postgres.
# MAGIC
# MAGIC The notebook can be rerun: it creates what is missing (the database, the pgvector extension, the
# MAGIC synced table) and otherwise starts a sync and waits for it. Run it on serverless in the workspace, or
# MAGIC from VS Code through databricks-connect with `DATABRICKS_CONFIG_PROFILE=llmops`. Each run costs one
# MAGIC pipeline run and the Lakebase compute while it writes and is queried; nothing is embedded.

# COMMAND ----------

import time

import psycopg
from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from databricks.sdk.service.postgres import (
    Database,
    DatabaseDatabaseSpec,
    EndpointType,
    NewPipelineSpec,
    SyncedTable,
    SyncedTableSyncedTableSpec,
    SyncedTableSyncedTableSpecPgSpecificType,
    SyncedTableSyncedTableSpecSyncedTableSchedulingPolicy,
    SyncedTableSyncedTableSpecTypeOverride,
)
from psycopg import sql

from vehicle_rules.chunks import TABLE as CHUNKS_TABLE
from vehicle_rules.config import get_env, load_config
from vehicle_rules.ingest import spark_session

config = load_config(env=get_env())
spark = spark_session()
w = WorkspaceClient()

branch = f"projects/{config.lakebase_instance}/branches/production"
source = f"{config.full_schema_name}.{CHUNKS_TABLE}"
synced = f"{config.full_schema_name}.{config.synced_table}"
# GTE Large, the embedding model of regulation_chunks, gives 1024 dimensions.
DIMENSIONS = 1024

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. The source
# MAGIC
# MAGIC The synced table needs a stable, unique, non-null primary key, populated vectors and the change data feed.

# COMMAND ----------

check = spark.sql(f"""
    SELECT count(*) AS chunks, count(DISTINCT id) AS ids, count_if(id IS NULL) AS null_ids,
           count_if(embedding IS NULL) AS null_vectors, min(size(embedding)) AS min_dims,
           max(size(embedding)) AS max_dims
    FROM {source}
""").first()
print(check.asDict())
assert check.chunks == check.ids and not check.null_ids, "id is not a unique, non-null key"
assert not check.null_vectors and check.min_dims == check.max_dims == DIMENSIONS, "missing or malformed vectors"
properties = {r.key: r.value for r in spark.sql(f"SHOW TBLPROPERTIES {source}").collect()}
assert properties.get("delta.enableChangeDataFeed") == "true", "the change data feed is off"
source_version = spark.sql(f"DESCRIBE HISTORY {source} LIMIT 1").first().version
print(f"{source}: version {source_version}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. The database and pgvector
# MAGIC
# MAGIC Onboarding creates the Lakebase project but no application database and no extensions. The connection
# MAGIC uses a short-lived credential from the SDK, never a token in the code.

# COMMAND ----------

database = next(
    (d for d in w.postgres.list_databases(parent=branch) if d.status.postgres_database == config.lakebase_database),
    None,
)
if database is None:
    database = w.postgres.create_database(
        parent=branch, database=Database(spec=DatabaseDatabaseSpec(postgres_database=config.lakebase_database))
    ).wait()
    print(f"Created {database.name}")
print(f"Database: {database.name}")

endpoint = next(
    e
    for e in w.postgres.list_endpoints(parent=branch)
    if e.status.endpoint_type == EndpointType.ENDPOINT_TYPE_READ_WRITE
)


def connect() -> psycopg.Connection:
    """Connect to the database as the current identity, with a fresh credential."""
    return psycopg.connect(
        host=endpoint.status.hosts.host,
        dbname=config.lakebase_database,
        user=w.current_user.me().user_name,
        password=w.postgres.generate_database_credential(endpoint=endpoint.name).token,
        sslmode="require",
        autocommit=True,
    )


with connect() as conn:
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    print(conn.execute("SELECT extname, extversion FROM pg_extension ORDER BY extname").fetchall())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. The synced table
# MAGIC
# MAGIC A Delta array does not become a pgvector column by itself: without the type override `embedding` arrives
# MAGIC as JSONB, and the vector operators of item 04 would not work on it. An existing synced table gets a new
# MAGIC sync instead, and the notebook waits for that update, not for the table merely being online.

# COMMAND ----------


def wait_for_update(pipeline_id: str, update_id: str) -> None:
    """Wait for a pipeline update to finish; fail if it did not complete."""
    while (state := w.pipelines.get_update(pipeline_id=pipeline_id, update_id=update_id).update.state.value) not in (
        "COMPLETED",
        "FAILED",
        "CANCELED",
    ):
        print(f"Update {update_id}: {state}")
        time.sleep(20)
    assert state == "COMPLETED", f"Update {update_id} of pipeline {pipeline_id}: {state}"


try:
    table = w.postgres.get_synced_table(name=f"synced_tables/{synced}")
except NotFound:
    table = None

if table:
    update = w.pipelines.start_update(pipeline_id=table.status.pipeline_id)
    wait_for_update(table.status.pipeline_id, update.update_id)
else:
    spec = SyncedTableSyncedTableSpec(
        source_table_full_name=source,
        branch=branch,
        postgres_database=config.lakebase_database,
        primary_key_columns=["id"],
        scheduling_policy=SyncedTableSyncedTableSpecSyncedTableSchedulingPolicy.TRIGGERED,
        type_overrides=[
            SyncedTableSyncedTableSpecTypeOverride(
                column_name="embedding",
                pg_type=SyncedTableSyncedTableSpecPgSpecificType.PG_SPECIFIC_TYPE_VECTOR,
                size=DIMENSIONS,
            )
        ],
        create_database_objects_if_missing=True,
        new_pipeline_spec=NewPipelineSpec(
            storage_catalog=config.catalog, storage_schema=config.db_schema, budget_policy_id=config.usage_policy_id
        ),
    )
    w.postgres.create_synced_table(synced_table=SyncedTable(spec=spec), synced_table_id=synced).wait()
    # The initial copy runs after the resource exists.
    while (state := w.postgres.get_synced_table(name=f"synced_tables/{synced}").status.detailed_state.value).startswith(
        "SYNCED_TABLE_PROVISIONING"
    ):
        print(state)
        time.sleep(20)

table = w.postgres.get_synced_table(name=f"synced_tables/{synced}")
status = table.status
print(f"State: {status.detailed_state.value}")
print(f"Last sync: {status.last_sync_time}, Delta version {status.last_processed_commit_version} of {source_version}")
print(f"Pipeline: {status.pipeline_id}")
assert status.detailed_state.value.startswith("SYNCED_TABLE_ONLINE") and "FAILED" not in status.detailed_state.value
if status.message:
    print(status.message)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. What arrived in Postgres
# MAGIC
# MAGIC The primary key, the type of `embedding`, the row count against Delta, null vectors and the indexes.

# COMMAND ----------

with connect() as conn:
    pg_schema = conn.execute(
        "SELECT table_schema FROM information_schema.tables WHERE table_name = %s", (config.synced_table,)
    ).fetchone()[0]
    name = sql.Identifier(pg_schema, config.synced_table)
    qualified = f"{pg_schema}.{config.synced_table}"
    print(f"Postgres table: {qualified}")

    primary_key = conn.execute(
        """
        SELECT a.attname FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
        WHERE i.indrelid = %s::regclass AND i.indisprimary
        """,
        (qualified,),
    ).fetchall()
    print(f"Primary key: {[r[0] for r in primary_key]}")

    vector_type = conn.execute(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute WHERE attrelid = %s::regclass AND attname = %s",
        (qualified, "embedding"),
    ).fetchone()[0]
    print(f"embedding: {vector_type}")

    rows, null_vectors = conn.execute(
        sql.SQL("SELECT count(*), count(*) FILTER (WHERE embedding IS NULL) FROM {}").format(name)
    ).fetchone()
    by_granularity = conn.execute(
        sql.SQL("SELECT granularity, count(*) FROM {} GROUP BY granularity ORDER BY granularity").format(name)
    ).fetchall()
    print(f"Rows: {rows} (Delta: {check.chunks}), null vectors: {null_vectors}, by granularity: {by_granularity}")

    indexes = conn.execute(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
        (pg_schema, config.synced_table),
    ).fetchall()
    for index in indexes:
        print(index)

assert [r[0] for r in primary_key] == ["id"], "the primary key is not id"
assert vector_type == f"vector({DIMENSIONS})", f"embedding is {vector_type}, not a pgvector column"
assert rows == check.chunks and not null_vectors, "Postgres does not hold every chunk with its vector"
