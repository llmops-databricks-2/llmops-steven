# Databricks notebook source
# /// script
# dependencies = [
#   "..",
# ]
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # Week 2: the chunks of the vehicle rules
# MAGIC
# MAGIC Homework items 01 and 02: inspect the chunk table, compare chunks with their source and note what
# MAGIC structure is lost; review the chunk sizes, try an alternative strategy and check the embedding model's
# MAGIC input limit.
# MAGIC
# MAGIC `regulation_chunks` holds the translated articles at two granularities, article and paragraph
# MAGIC (`vehicle_rules.chunks`): the alternative strategy is the other granularity.
# MAGIC
# MAGIC Run it on serverless in the workspace, or from VS Code through databricks-connect with
# MAGIC `DATABRICKS_CONFIG_PROFILE=llmops`. It makes 13 small calls to the embedding model.

# COMMAND ----------

import math

import pandas as pd
from databricks.sdk import WorkspaceClient
from loguru import logger
from pyspark.sql import functions as F

from vehicle_rules.config import get_env, load_config
from vehicle_rules.ingest import spark_session

config = load_config(env=get_env())
spark = spark_session()
client = WorkspaceClient().serving_endpoints.get_open_ai_client()
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 20)

schema = config.full_schema_name
chunks = spark.table(f"{schema}.regulation_chunks")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. The chunk table

# COMMAND ----------

print(
    chunks.groupBy("granularity")
    .agg(
        F.count("*").alias("chunks"),
        F.countDistinct("annex", "article").alias("articles in an annex"),
        F.count("embedding").alias("embedded"),
        F.min(F.size("embedding")).alias("dimensions"),
    )
    .toPandas()
)
print(chunks.where("granularity = 'paragraph'").groupBy("vehicle_type").count().orderBy(F.desc("count")).toPandas())

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Chunks against their source
# MAGIC
# MAGIC Article 5.2.47 on seat belts. Paragraph 2 has no inspection method of its own in the source:
# MAGIC paragraph 1 states it for "Leden 1 en 2". Its chunk gets the method too.

# COMMAND ----------

for row in chunks.where("annex IS NULL AND article = '5.2.47'").orderBy("id").collect():
    print(f"--- {row.id}\n{row.text}\n")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Lost structure
# MAGIC
# MAGIC Counted on the paragraph chunks: a shared inspection method added, a figure replaced by a
# MAGIC placeholder, another table flattened to cells separated by `|`, a reference to another paragraph
# MAGIC (not counting the inspection methods, whose "Paragraphs 1 and 2:" names the paragraphs they cover),
# MAGIC and paragraphs without a number.

# COMMAND ----------

paragraphs = (
    spark.table(f"{schema}.regulation_articles")
    .select("annex", "article", F.explode("paragraphs").alias("p"))
    .select("annex", "article", F.col("p.paragraph").alias("paragraph"), F.col("p.text_nl").alias("source_nl"))
)
p = chunks.where("granularity = 'paragraph'").alias("c")
same = (
    p.annex.eqNullSafe(paragraphs.annex)
    & (p.article == paragraphs.article)
    & p.paragraph.eqNullSafe(paragraphs.paragraph)
)
body = F.expr("filter(split(c.text_en, '\\n'), line -> NOT startswith(line, 'Inspection method'))")
print(
    p.join(paragraphs, same)
    .agg(
        F.count("*").alias("paragraph chunks"),
        F.sum(
            (~F.col("source_nl").contains("Wijze van keuren") & F.col("c.text_nl").contains("Wijze van keuren")).cast(
                "int"
            )
        ).alias("with a shared method added"),
        F.sum(F.col("c.text_nl").contains("[afbeelding]").cast("int")).alias("with a figure"),
        F.sum(F.col("c.text_nl").contains(" | ").cast("int")).alias("with table cells"),
        F.sum(F.exists(body, lambda line: line.rlike(r"(?i)\bparagraphs? \d")).cast("int")).alias(
            "naming another paragraph"
        ),
        F.sum(F.col("c.paragraph").isNull().cast("int")).alias("without a number"),
    )
    .toPandas()
    .T
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Chunk sizes
# MAGIC
# MAGIC In characters of `text`, the embedded text. Tokens are estimated at 4.4 characters a token, as
# MAGIC measured in section 5.

# COMMAND ----------

labels = ["min", "p10", "median", "p90", "p99", "max"]
sizes = pd.DataFrame(
    {
        g: chunks.where(f"granularity = '{g}'")
        .select(F.length("text").alias("chars"))
        .approxQuantile("chars", [0.0, 0.1, 0.5, 0.9, 0.99, 1.0], 0.0)
        for g in ("article", "paragraph")
    },
    index=labels,
).T
print(sizes.assign(**{f"{c} (tokens)": (sizes[c] / 4.4).round() for c in ["median", "p99", "max"]}))

largest = chunks.select("id", F.length("text").alias("chars"), "text").orderBy(F.desc("chars")).limit(5).toPandas()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. The embedding model's input limit
# MAGIC
# MAGIC `databricks-gte-large-en` serves GTE Large (En) v1.5: 1024 dimensions, at most 8192 tokens. The
# MAGIC embeddings API reports the tokens it read: longer input is cut at 8192 tokens without an error.


# COMMAND ----------


def embed(text: str) -> tuple[list[float], int]:
    """Embed a text with the chunks' model; return the vector and the tokens the model read."""
    response = client.embeddings.create(model=config.embedding_endpoint, input=text)
    return response.data[0].embedding, response.usage.prompt_tokens


for row in largest.itertuples():
    _, tokens = embed(row.text)
    logger.info(f"{row.id}: {row.chars} characters, {tokens} tokens ({row.chars / tokens:.1f} characters a token)")

too_long = largest.text[0] * 10
_, tokens = embed(too_long)
logger.info(f"{len(too_long)} characters: the model read {tokens} tokens, without an error")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Article or paragraph: a first comparison
# MAGIC
# MAGIC Owner questions with the articles that answer them, picked by hand, searched by cosine similarity
# MAGIC at each granularity, in pandas on the Delta table's vectors: before the Lakebase synced table of item
# MAGIC 03. The rank is that of the best-ranked article that answers, counting articles rather than chunks.
# MAGIC Seven questions are a sanity check, not an evaluation.

# COMMAND ----------

questions = [
    ("Is it allowed to have tinted front side windows, and does the APK check them?", {(None, "5.2.42")}),
    ("My car is from 1987: does it need seat belts in the back?", {(None, "5.2.47")}),
    ("How much rust damage is allowed on a car with a self-supporting body?", {("VIII", "5"), ("VIII", "11")}),
    ("How is the date of first admission determined for an imported car?", {(None, "2.2"), ("II", "4"), ("II", "5")}),
    (
        "Which damage to the windscreen is allowed in the driver's field of view?",
        {(None, "5.2.42"), ("VIII", "91"), ("VIII", "92"), ("VIII", "93"), ("VIII", "94")},
    ),
    ("What is the maximum noise level of a passenger car?", {("VIII", "30"), ("VIII", "31"), ("VIII", "32")}),
    (
        "What does the APK check on the exhaust emissions of a petrol car?",
        {(None, "5.2.11"), ("VIII", "40"), ("VIII", "41"), ("VIII", "42"), ("VIII", "43")},
    ),
]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors."""
    return sum(x * y for x, y in zip(a, b, strict=True)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))


vectors = chunks.select("granularity", "annex", "article", "embedding").toPandas()
results = []
for question, answers in questions:
    query, _ = embed(question)
    scored = vectors.assign(score=[cosine(query, v) for v in vectors.embedding])
    result = {"question": question[:60]}
    for granularity in ("article", "paragraph"):
        ranked = scored[scored.granularity == granularity].sort_values("score", ascending=False)
        ranked = ranked.drop_duplicates(["annex", "article"]).reset_index(drop=True)
        keys = list(zip(ranked.annex.where(ranked.annex.notna(), None), ranked.article, strict=True))
        result[f"{granularity} rank"] = next((i + 1 for i, key in enumerate(keys) if key in answers), None)
        result[f"{granularity} top 3"] = ", ".join(f"{a + ' ' if a else ''}{n}" for a, n in keys[:3])
    results.append(result)
print(pd.DataFrame(results))
