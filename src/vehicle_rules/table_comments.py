"""Describe the RDW tables in Unity Catalog, for Genie and for anyone reading them.

A bundle cannot manage a table, so the comments live here and the `table_comments` job applies them. The tables
came with a comment on every column. Only what needs saying differently is here: the tables themselves, and the
columns whose comment misleads for this project. Anything else stays as it is.

Run it again after reloading a table: `CREATE TABLE … AS SELECT` does not keep comments set here. It only changes
comments, never the data. The tables are in dev only, so it fails in acc and prd until they are loaded there.

Run it as the job (resources/table_comments.job.yml), or from the repo root through databricks-connect:
`DATABRICKS_CONFIG_PROFILE=llmops-new uv run python -m vehicle_rules.table_comments`.
"""

from pyspark.sql import SparkSession

from vehicle_rules.config import get_env, load_config
from vehicle_rules.ingest import spark_session

VEHICLES_TABLE = "rdw_registered_vehicles"
FUEL_TABLE = "rdw_registered_vehicles_fuel"

TABLE_COMMENTS = {
    VEHICLES_TABLE: (
        "One row per passenger car that is registered in the Netherlands, not exported, insured and not a taxi. "
        "RDW open data, a snapshot with data up to 2026-10-11. Find a car by license_plate: six uppercase letters "
        "and digits, without dashes. Other vehicle types, such as vans and motorcycles, are not in this table."
    ),
    FUEL_TABLE: (
        "One row per fuel per car, so a hybrid has two rows. RDW open data, the same snapshot as "
        "rdw_registered_vehicles, to join on license_plate. 609 cars have no row here."
    ),
}

COLUMN_COMMENTS = {
    VEHICLES_TABLE: {
        "vehicle_type": "National vehicle type, in Dutch. Every row of this table is Personenauto, a passenger car.",
        "first_admission_date": (
            "Date of first admission anywhere in the world. This is the date a vehicle was put into service under "
            "article 2.2 of the Regeling voertuigen: use it for rules that depend on a first-use date."
        ),
        "first_registration_date_nl": (
            "Date of first registration in the Netherlands, which can be later than first_admission_date, as for an "
            "imported car. "
            "Do not use it for rules that depend on a first-use date."
        ),
    },
    FUEL_TABLE: {
        "fuel_description": "Fuel or energy source, in Dutch, such as Benzine, Diesel, Elektriciteit, LPG or Waterstof.",
    },
}


def _literal(comment: str) -> str:
    """Quote a comment as a SQL string."""
    return "'" + comment.replace("\\", "\\\\").replace("'", "\\'") + "'"


def statements(schema: str) -> list[str]:
    """Return the statements that set the comments in a schema, safe to run again."""
    tables = [f"COMMENT ON TABLE {schema}.{t} IS {_literal(c)}" for t, c in TABLE_COMMENTS.items()]
    columns = [
        f"ALTER TABLE {schema}.{table} ALTER COLUMN {column} COMMENT {_literal(comment)}"
        for table, comments in COLUMN_COMMENTS.items()
        for column, comment in comments.items()
    ]
    return tables + columns


def apply(spark: SparkSession, schema: str) -> None:
    """Set the comments in a schema."""
    for statement in statements(schema):
        spark.sql(statement)
        print(statement[:100])


def main(env: str = "dev") -> None:
    """Set the comments on the RDW tables of an environment."""
    apply(spark_session(), load_config(env=env).full_schema_name)


if __name__ == "__main__":
    main(get_env())
