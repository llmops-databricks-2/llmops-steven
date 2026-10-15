# Databricks notebook source
# /// script
# dependencies = [
#   "..",
# ]
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # Week 1: the course model service on vehicle rules
# MAGIC
# MAGIC Homework item 03: call the course chat model through Unity Gateway, authenticated with
# MAGIC `WorkspaceClient`, on a few prompts from the vehicle rules assistant (`docs/use_case.md`), and
# MAGIC record the responses, their token usage and the limitations we observe. It follows the course's
# MAGIC notebook 1.1: a short-lived token, the OpenAI client on the gateway URL, and the Unified Responses API.
# MAGIC
# MAGIC The article texts come from `regulation_articles` and the car from `rdw_registered_vehicles`, both in
# MAGIC the workspace. The car is described by its register attributes only, never by its plate.
# MAGIC
# MAGIC Run it on serverless in the workspace, or from VS Code through databricks-connect with
# MAGIC `DATABRICKS_CONFIG_PROFILE=llmops`. It makes eight billable calls to the course service.

# COMMAND ----------

import json
import time
from datetime import UTC, datetime

import pandas as pd
from databricks.sdk import WorkspaceClient
from loguru import logger
from openai import APIStatusError, OpenAI
from pyspark.errors import PySparkException
from pyspark.sql import functions as F

from vehicle_rules.config import get_env, load_config
from vehicle_rules.ingest import spark_session

# COMMAND ----------

config = load_config(env=get_env())
spark = spark_session()
workspace_client = WorkspaceClient()

logger.info(f"Workspace: {workspace_client.config.host}")
logger.info(f"Unity Catalog model service: {config.llm_endpoint}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## The client
# MAGIC
# MAGIC As in notebook 1.1, `WorkspaceClient` uses the configured Databricks authentication to create a
# MAGIC token that is valid for 20 minutes; it authenticates to Databricks, not to the model provider. The
# MAGIC token is never printed, and the last cell revokes it. If your identity may not create tokens, use
# MAGIC notebook 1.4's `workspace_client.config.authenticate()` instead, which reuses the current credential.

# COMMAND ----------

token = workspace_client.tokens.create(comment="week 1 model service experiment", lifetime_seconds=1200)

client = OpenAI(
    api_key=token.token_value,
    base_url=f"{workspace_client.config.host}/ai-gateway/mlflow/v1",
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## The context: articles and a car
# MAGIC
# MAGIC The car is an imported one: put into service in 1987, but first registered in the Netherlands from
# MAGIC 1999. Article 2.2 says the date that counts is the first admission, so the 1987 rules apply. The
# MAGIC Dutch registration date is held back for the follow-up question.

# COMMAND ----------

schema = config.full_schema_name
articles = spark.table(f"{schema}.regulation_articles").where(
    (F.col("bwb_id") == config.regulations[0].bwb_id) & F.col("annex").isNull()
)


def article(number: str, language: str = "en") -> str:
    """Return a main-text article of the Regeling voertuigen with its number, in English or Dutch."""
    text = articles.where(F.col("article") == number).select(f"text_{language}").first()[0]
    return f"{'Article' if language == 'en' else 'Artikel'} {number}\n{text}"


fuels = (
    spark.table(f"{schema}.rdw_registered_vehicles_fuel")
    .groupBy("license_plate")
    .agg(F.array_sort(F.collect_list("fuel_description")).alias("fuels"))
)
car = (
    spark.table(f"{schema}.rdw_registered_vehicles")
    .where((F.year("first_admission_date") == 1987) & (F.year("first_registration_date_nl") >= 1999))
    .join(fuels, "license_plate")
    .orderBy("license_plate")
    .select(
        "vehicle_type",
        "european_vehicle_category",
        "make",
        "trade_name",
        "first_admission_date",
        "first_registration_date_nl",
        "number_of_seats",
        "number_of_doors",
        "max_design_speed",
        "fuels",
    )
    .first()
    .asDict()
)
registered_nl = car.pop("first_registration_date_nl")
car_description = "\n".join(f"{key}: {value}" for key, value in car.items())
logger.info(f"The car:\n{car_description}\nfirst registered in the Netherlands: {registered_nl}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Calling the model service
# MAGIC
# MAGIC `ask` sends a conversation in the Responses format of notebook 1.1, with the project's system prompt
# MAGIC first, and keeps a record of each call: its status, token usage, latency and an estimate of its DBUs.
# MAGIC `course_ops.gateway.chat` routes to GPT OSS 120B, a reasoning model whose reasoning tokens count as
# MAGIC output tokens. The rates are notebook 1.1's pay-per-token rates for that model; the service's actual
# MAGIC charges depend on its routing.
# MAGIC
# MAGIC A status other than `completed` means the answer is cut off or missing. A policy denial can arrive
# MAGIC as HTTP 200, so read the answer as well as the status.

# COMMAND ----------

# GPT OSS 120B pay-per-token rates in DBU per million tokens, from notebook 1.1 (checked 12 October 2026).
INPUT_DBU_PER_1M, OUTPUT_DBU_PER_1M = 2.143, 8.571
started = datetime.now(UTC)
results = []


def message(role: str, text: str) -> dict:
    """Build an input message of the Responses API."""
    return {"role": role, "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": text}]}


def ask(name: str, turns: list[dict], effort: str = "low", max_output_tokens: int = 1024, **kwargs: object) -> str:
    """Call the model service with a conversation, log the answer and usage, and record the call."""
    start = time.perf_counter()
    try:
        response = client.responses.create(
            model=config.llm_endpoint,
            input=[message("system", config.system_prompt), *turns],
            reasoning={"effort": effort},
            max_output_tokens=max_output_tokens,
            **kwargs,
        )
    except APIStatusError as e:
        # A rejected request is an observation too.
        logger.warning(f"{name}: HTTP {e.status_code}: {e.message}")
        results.append(
            {"call": name, "effort": effort, "max_output_tokens": max_output_tokens, "status": e.status_code}
        )
        return ""
    seconds = time.perf_counter() - start
    usage = response.usage
    details = usage.output_tokens_details if usage else None
    status = response.status
    if response.incomplete_details:
        status = f"{status}: {response.incomplete_details.reason}"
    results.append(
        {
            "call": name,
            "effort": effort,
            "max_output_tokens": max_output_tokens,
            "status": status,
            "input_tokens": usage.input_tokens if usage else None,
            "output_tokens": usage.output_tokens if usage else None,
            "reasoning_tokens": details.reasoning_tokens if details else None,
            "seconds": round(seconds, 1),
            "dbu": (usage.input_tokens * INPUT_DBU_PER_1M + usage.output_tokens * OUTPUT_DBU_PER_1M) / 1e6
            if usage
            else None,
        }
    )
    logger.info(f"{name} ({status}, {seconds:.1f} s): {results[-1]}")
    logger.info(f"Answer:\n{response.output_text}")
    return response.output_text


# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Closed book
# MAGIC
# MAGIC The question an owner would ask, without any article text: what does the model know of the Dutch
# MAGIC rules, and does it name articles that exist? Correct: for a car put into service in 1987, belts are
# MAGIC required only on the driver's seat and the seat next to it if that seat is by a door (article 5.2.47,
# MAGIC paragraph 3), not on the rear seats.

# COMMAND ----------

closed_book = (
    "Under the Dutch vehicle rules, does my passenger car, first put into service in 1987, need seatbelts "
    "on the rear seats? Name the article you base this on."
)
closed_book_answer = ask("1 closed book", [message("user", closed_book)])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Grounded, in English
# MAGIC
# MAGIC The same question with article 5.2.47 and the car's register data. Correct: paragraph 3 (driver's seat,
# MAGIC and the seat next to it if by a door), 5 and 6 (any belts present must be sound and work) apply;
# MAGIC 1, 2, 7 and 8 do not, and 4 only if the maximum design speed is at most 25 km/h.
# MAGIC
# MAGIC It runs three times: reasoning effort low, effort medium, and effort low with at most 256 output
# MAGIC tokens, to see whether reasoning uses up that budget before the answer.

# COMMAND ----------

grounded = [
    message(
        "user",
        f"My car, from the Dutch vehicle register:\n{car_description}\n\n{article('5.2.47')}\n\n"
        "Which paragraphs of this article apply to my car, and which seats must have seatbelts? "
        "Answer only from the article above, and cite its paragraph numbers.",
    )
]
grounded_answer = ask("2 grounded, English", grounded)

# COMMAND ----------

medium_answer = ask("2 grounded, English", grounded, effort="medium")
short_answer = ask("2 grounded, English", grounded, max_output_tokens=256)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Grounded, in Dutch
# MAGIC
# MAGIC The same question with the official Dutch text of the article: does the chat model need the
# MAGIC translation, and how do the input tokens compare? The correct answer is that of prompt 2.

# COMMAND ----------

dutch_answer = ask(
    "3 grounded, Dutch",
    [
        message(
            "user",
            f"My car, from the Dutch vehicle register:\n{car_description}\n\n{article('5.2.47', 'nl')}\n\n"
            "Which paragraphs of this article apply to my car, and which seats must have seatbelts? "
            "Answer only from the article above, and cite its paragraph numbers.",
        )
    ],
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Conditions as JSON
# MAGIC
# MAGIC Later preprocessing will need each requirement's conditions as data. Article 5.2.11 (exhaust and
# MAGIC emissions) has dates, engine types, a mass limit and an exception in another paragraph: paragraph 12
# MAGIC lifts paragraphs 7 and 10 for diesels with a particulate filter. The request asks for structured output
# MAGIC with a JSON schema; whether the service supports that is part of the test.

# COMMAND ----------

CONDITIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "paragraphs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "paragraph": {"type": "string"},
                    "put_into_service_after": {"type": ["string", "null"], "description": "ISO date, exclusive"},
                    "put_into_service_before": {"type": ["string", "null"], "description": "ISO date, exclusive"},
                    "engine_types": {"type": "array", "items": {"type": "string"}},
                    "max_permissible_mass_kg": {"type": ["number", "null"]},
                    "other_conditions": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "paragraph",
                    "put_into_service_after",
                    "put_into_service_before",
                    "engine_types",
                    "max_permissible_mass_kg",
                    "other_conditions",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["paragraphs"],
    "additionalProperties": False,
}

conditions = ask(
    "4 conditions as JSON",
    [
        message(
            "user",
            f"{article('5.2.11')}\n\nFor each paragraph of this article, list the conditions under which it "
            "applies to a passenger car. Leave a field empty when the paragraph sets no such condition.",
        )
    ],
    max_output_tokens=4096,
    text={"format": {"type": "json_schema", "name": "conditions", "schema": CONDITIONS_SCHEMA, "strict": True}},
)
try:
    logger.info(f"Valid JSON with {len(json.loads(conditions)['paragraphs'])} paragraphs")
except (json.JSONDecodeError, KeyError, TypeError) as e:
    logger.warning(f"Not the requested JSON: {e!r}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. An owner's words
# MAGIC
# MAGIC Owners say "tinted windows", the regulation says light transmission (article 5.2.42). Correct: the
# MAGIC windscreen and the side windows next to the driver must let through at least 55% of light
# MAGIC (paragraph 3), and the periodic inspection (APK) does not check this. The rear window is only
# MAGIC regulated if the car has no right exterior mirror (paragraphs 4 and 5).

# COMMAND ----------

tinted_answer = ask(
    "5 tinted windows",
    [
        message(
            "user",
            f"{article('5.2.42')}\n\nCan I have tinted windows on my passenger car, and will the APK check them?",
        )
    ],
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. A follow-up turn
# MAGIC
# MAGIC The conversation of prompt 2 continues with the Dutch registration date and article 2.2. Correct: the
# MAGIC answer does not change, because the date a car was put into service is its first admission, which
# MAGIC can be abroad; the later Dutch registration does not move it to the rules for 1990 to 2000.

# COMMAND ----------

follow_up_answer = ask(
    "6 follow-up",
    [
        *grounded,
        message("assistant", grounded_answer),
        message(
            "user",
            f"I forgot to mention that the car was imported: it was first registered in the Netherlands on "
            f"{registered_nl}. Does that change your answer?\n\n{article('2.2')}",
        ),
    ],
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Usage
# MAGIC
# MAGIC The calls as recorded by the client, and as recorded by the gateway in `system.ai_gateway.usage`. By
# MAGIC default only account and metastore admins can read that system table, and its rows may arrive later.

# COMMAND ----------

summary = pd.DataFrame(results)
logger.info(f"Calls:\n{summary.to_string()}")
logger.info(
    f"Total: {summary['input_tokens'].sum():,.0f} input and {summary['output_tokens'].sum():,.0f} output tokens"
)
logger.info(f"Estimated DBUs: {summary['dbu'].sum():.4f}")

# COMMAND ----------

try:
    gateway_usage = (
        spark.table("system.ai_gateway.usage")
        .where((F.col("requester") == workspace_client.current_user.me().user_name) & (F.col("event_time") >= started))
        .select(
            "event_time",
            "service_name",
            "destination_model",
            "api_type",
            "status_code",
            "input_tokens",
            "output_tokens",
            "latency_ms",
        )
        .orderBy("event_time")
        .toPandas()
    )
    logger.info(f"Gateway usage records:\n{gateway_usage.to_string()}")
except PySparkException as e:
    # The message without the server's JVM stack trace.
    logger.warning(f"Cannot read system.ai_gateway.usage: {str(e).split('JVM stacktrace:')[0].strip()}")

# COMMAND ----------

# Revoke the token now rather than leave it valid until it expires.
workspace_client.tokens.delete(token.token_info.token_id)
