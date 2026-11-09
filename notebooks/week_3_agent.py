# Databricks notebook source
# /// script
# dependencies = [
#   "..",
# ]
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # Week 3: the agent at work
# MAGIC
# MAGIC The evidence for the homework: which tools the model selects, a successful result, a controlled failure,
# MAGIC and memory that persists in Lakebase and stays within its session. The design is in `docs/week-3.md`.
# MAGIC
# MAGIC Run it on serverless in the workspace, or from VS Code with `DATABRICKS_CONFIG_PROFILE=llmops-new`. A run is
# MAGIC 15 turns: about 75,000 tokens of the chat model, a few embedding calls and one Genie question. It adds
# MAGIC its sessions to `session_messages`.

# COMMAND ----------

from databricks.sdk import WorkspaceClient

from vehicle_rules.agent import Turn, VehicleRulesAgent, gateway_client
from vehicle_rules.config import get_env, load_config
from vehicle_rules.memory import chat, ensure_table, load_messages, new_session_id
from vehicle_rules.search import connect
from vehicle_rules.tools import genie_tools, make_tools
from vehicle_rules.vehicles import VEHICLES_TABLE, lookup_car

config = load_config(env=get_env())
w = WorkspaceClient()
conn = connect(config, w)
ensure_table(conn)
agent = VehicleRulesAgent(
    gateway_client(w),
    config,
    make_tools(conn, config, w) + genie_tools(config, w),
    lambda plate: lookup_car(config, w, plate),
)

OWNER = "notebook"
OTHER_OWNER = "notebook-other"


def ask(question: str, session_id: str | None = None) -> Turn:
    """Ask in a session, a new one if none is given, and print the tools the model called and its answer."""
    turn = chat(conn, agent, session_id or new_session_id(), OWNER, question)
    calls = [call["function"] for message in turn.messages for call in message.get("tool_calls", [])]
    print(f"\nQ: {question}\nTools: {[(call['name'], call['arguments']) for call in calls]}\nA: {turn.answer}")
    return turn


# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Tool selection and successful results
# MAGIC
# MAGIC The seven owner questions of week 2, without a plate: the model should search, and answer in general with
# MAGIC the conditions. Naming an article should select `get_article` instead.

# COMMAND ----------

for question in [
    "Is it allowed to have tinted front side windows, and does the APK check them?",
    "My car is from 1987: does it need seat belts in the back?",
    "How much rust damage is allowed on a car with a self-supporting body?",
    "How is the date of first admission determined for an imported car?",
    "Which damage to the windscreen is allowed in the driver's field of view?",
    "What is the maximum noise level of a passenger car?",
    "What does the APK check on the exhaust emissions of a petrol car?",
    "What does article 5.2.47 require?",
]:
    ask(question)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Controlled failures
# MAGIC
# MAGIC An unknown plate, an article that does not exist, and a question outside the regulation: the agent should say
# MAGIC so, not guess.

# COMMAND ----------

for question in [
    "Does my car need seat belts in the back? Plate ZZ999Z",
    "What does article 999 require?",
    "How much road tax do I pay for a petrol car?",
]:
    ask(question)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. A fleet question through Genie

# COMMAND ----------

ask("How many petrol cars from 1987 are registered in the Netherlands?")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Memory
# MAGIC
# MAGIC The second question names neither the car nor the plate, so it can only be answered from the session. In a new
# MAGIC session the agent should not know the car, and another owner should find nothing in the first session.

# COMMAND ----------

plate = w.statement_execution.execute_statement(
    warehouse_id=config.warehouse_id,
    statement=f"""
        SELECT license_plate FROM {config.full_schema_name}.{VEHICLES_TABLE}
        WHERE YEAR(first_admission_date) = 1987 AND number_of_seats = 5 AND number_of_doors = 4
        ORDER BY license_plate LIMIT 1
    """,
    wait_timeout="50s",
).result.data_array[0][0]

session_id = new_session_id()
ask(f"Does my car need seat belts in the back? Plate {plate}", session_id)
ask("And when was my car first admitted?", session_id)
ask("And when was my car first admitted?")

stored = load_messages(conn, session_id, OWNER)
print(f"\nMessages stored in the session: {len(stored)}")
assert stored, "the session was not saved"
assert load_messages(conn, session_id, OTHER_OWNER) == [], "another owner can read the session"

# COMMAND ----------

conn.close()
