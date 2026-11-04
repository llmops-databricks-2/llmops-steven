# Databricks notebook source
# /// script
# dependencies = [
#   "..",
# ]
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # Week 2: searching the chunks
# MAGIC
# MAGIC Homework item 04: run semantic, filtered and hybrid search on the same questions and capture what
# MAGIC changes in the returned evidence, at each granularity, on the synced table in Postgres.
# MAGIC
# MAGIC The seven owner questions are those of `notebooks/week_2_chunks.py`, where their articles were picked by
# MAGIC hand. Three more name an exact term, where keywords should help most. Ten questions are a sanity check,
# MAGIC not an evaluation.
# MAGIC
# MAGIC Run it on serverless in the workspace, or from VS Code through databricks-connect with
# MAGIC `DATABRICKS_CONFIG_PROFILE=llmops-new`. Each run makes ten small calls to the embedding model and wakes
# MAGIC the Lakebase compute for the queries. It only reads.

# COMMAND ----------

import pandas as pd
from databricks.sdk import WorkspaceClient

from vehicle_rules.config import get_env, load_config
from vehicle_rules.search import connect, embed_question, hybrid, semantic

config = load_config(env=get_env())
w = WorkspaceClient()

GRANULARITIES = ("article", "paragraph")
# All the questions are about a passenger car; the filter also keeps the chunks that apply to any vehicle.
VEHICLE_TYPE = "Passenger cars"
# Enough chunks to count ten or so articles at paragraph granularity, where an article has several chunks. A rank
# is empty if no answer is among them.
CANDIDATES = 200
# The chunks in which to count the requirements of other vehicle types, which is what the filter should remove.
TOP_CHUNKS = 5

OWNER_QUESTIONS = [
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
EXACT_TERM_QUESTIONS = [
    ("What does article 5.2.47 require?", {(None, "5.2.47")}),
    ("Which lambda value must a petrol car stay within?", {("VIII", "42"), ("VIII", "43")}),
    ("When does a passenger car need a tachograph?", {(None, "5.2.15")}),
]
QUESTIONS = OWNER_QUESTIONS + EXACT_TERM_QUESTIONS

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. One question first
# MAGIC
# MAGIC This is the first run of the SQL against Postgres. Stop here if it fails. Keywords must score too: if every
# MAGIC `text_score` were zero, hybrid search would only repeat semantic search.

# COMMAND ----------

question = QUESTIONS[0][0]
embeddings = {question: embed_question(config, w, question)}

with connect(config, w) as conn:
    chunks = hybrid(conn, config, question, embeddings[question], "article", vehicle_type=VEHICLE_TYPE)

print(pd.DataFrame(chunks).drop(columns="text").head(TOP_CHUNKS).to_string())
assert any(chunk["text_score"] > 0 for chunk in chunks), "no keyword score: hybrid would repeat semantic search"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Every search on every question
# MAGIC
# MAGIC Each question is embedded once. As in `notebooks/week_2_chunks.py`, the rank is that of the best-ranked
# MAGIC article that answers, counting articles rather than chunks.

# COMMAND ----------


def articles(chunks: list[dict]) -> list[tuple[str | None, str]]:
    """Return the articles of chunks, each once, in the order of its best chunk."""
    return list(dict.fromkeys((chunk["annex"], chunk["article"]) for chunk in chunks))


def article_label(article: tuple[str | None, str]) -> str:
    """Name an article: "5.2.42", or "VIII 30" in an annex."""
    annex, number = article
    return f"{annex} {number}" if annex else number


def evidence(question: str, answers: set, search: str, granularity: str, chunks: list[dict]) -> dict:
    """Summarize what a search returned: the rank of the answer, its first articles, other vehicle types."""
    found = articles(chunks)
    return {
        "question": question[:60],
        "search": search,
        "granularity": granularity,
        "rank": next((rank for rank, article in enumerate(found, 1) if article in answers), None),
        "top 3": ", ".join(article_label(article) for article in found[:3]),
        "other types": sum(c["vehicle_type"] not in (None, VEHICLE_TYPE) for c in chunks[:TOP_CHUNKS]),
    }


embeddings |= {question: embed_question(config, w, question) for question, _ in QUESTIONS if question not in embeddings}

searches = {
    "semantic": lambda conn, _, vector, granularity: semantic(conn, config, vector, granularity, limit=CANDIDATES),
    "filtered": lambda conn, _, vector, granularity: semantic(
        conn, config, vector, granularity, vehicle_type=VEHICLE_TYPE, limit=CANDIDATES
    ),
    "hybrid": lambda conn, question, vector, granularity: hybrid(
        conn, config, question, vector, granularity, limit=CANDIDATES
    ),
    "filtered hybrid": lambda conn, question, vector, granularity: hybrid(
        conn, config, question, vector, granularity, vehicle_type=VEHICLE_TYPE, limit=CANDIDATES
    ),
}

with connect(config, w) as conn:
    results = pd.DataFrame(
        evidence(question, answers, name, granularity, search(conn, question, embeddings[question], granularity))
        for question, answers in QUESTIONS
        for granularity in GRANULARITIES
        for name, search in searches.items()
    )
results["rank"] = results["rank"].astype("Int64")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. What changed in the evidence
# MAGIC
# MAGIC The rank of the answer for each search, then the first articles each search returned, and how many of the
# MAGIC first chunks belong to other vehicle types. The last three questions are the exact-term ones.

# COMMAND ----------

question_labels = [question[:60] for question, _ in QUESTIONS]

for column in ("rank", "top 3", "other types"):
    for granularity in GRANULARITIES:
        table = results[results.granularity == granularity].pivot(index="question", columns="search", values=column)
        print(f"\n{column}, {granularity}:")
        print(table.reindex(index=question_labels, columns=list(searches)).to_string())
