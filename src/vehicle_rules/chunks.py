"""Chunk the translated articles at two granularities, article and paragraph, and embed them.

Both granularities go to one table, regulation_chunks, told apart by `granularity`, so that retrieval can be
compared on each before choosing:

- article: one chunk per article, its whole text. An article too long for the embedding model, article 1.1
  with its definitions, is chunked by its paragraphs instead;
- paragraph: one chunk per paragraph of regulation_articles.paragraphs, as `ingest.paragraphs` splits it.
  An article without paragraphs is one paragraph, so each granularity covers every translated article.

A requirement whose inspection method is stated once for several paragraphs ("Leden 1 en 2: visuele
controle") gets that method in its paragraph chunk too, where the method's own range covers it.

Only the English is embedded, since the embedding model, GTE Large, is English-only, and only translated
articles are chunked: the rest of the regulation is outside the use case. `text`, what is embedded and
searched, starts with a line that places the chunk: its headings, without the chapter's or annex's title,
and its reference ("Division 2 Passenger cars › § 9 Bodywork › Article 5.2.47, paragraph 2"). In chapter 5,
`vehicle_type` holds the vehicle type of the article's division, to filter on.

Run it as the chunks task of the ingest job (resources/ingest.job.yml), after the ingest, or from the repo
root through databricks-connect: `DATABRICKS_CONFIG_PROFILE=llmops uv run python -m vehicle_rules.chunks`.
"""

import re
from collections import Counter

from pyspark.sql import functions as F

from vehicle_rules.config import get_env, load_config
from vehicle_rules.ingest import PARAGRAPH_NUMBER
from vehicle_rules.ingest import TABLE as ARTICLES_TABLE
from vehicle_rules.ingest import spark_session

TABLE = "regulation_chunks"
METHOD = "Wijze van keuren:"
# The paragraphs a shared inspection method names: "Leden 1 en 2:", "Lid 3:", "Leden 6 tot en met 9:".
METHOD_RANGE = re.compile(rf"{METHOD} (?:Leden|Lid) ([^:]{{1,60}}):")
# The divisions of chapter 5 that hold no vehicle type: general provisions and usage requirements.
GENERAL_DIVISIONS = {"1", "1a", "1b", "18"}
# Longer articles are chunked by their paragraphs at article granularity too. GTE Large reads at most 8192
# tokens and cuts the rest without an error; English legal text runs at about 4.4 characters a token. Only
# article 1.1 is longer (35,574 characters); the next is 9,349.
MAX_ARTICLE_CHARS = 20_000
SCHEMA = (
    "id string, granularity string, bwb_id string, version string, annex string, article string, "
    "paragraph string, chapter string, section string, vehicle_type string, headings array<string>, "
    "headings_en array<string>, effective_from date, text_nl string, text_en string, text string"
)


def covered(method: str) -> set[str]:
    """The paragraph numbers a shared inspection method names, such as {"1", "2"} for "Leden 1 en 2:"."""
    m = METHOD_RANGE.match(method)
    if not m:
        return set()
    names = m.group(1)
    numbers = set(re.findall(r"\d+[a-z]?", names))
    for first, last in re.findall(r"(\d+)[a-z]?\s+tot\s+(?:en\s+)?met\s+(?:lid\s+)?(\d+)", names):
        numbers |= {str(n) for n in range(int(first), int(last) + 1)}
    return numbers


def with_shared_methods(paragraphs: list[dict]) -> list[dict]:
    """Add a shared inspection method to each paragraph it covers that has none of its own.

    The method is the last one stated above the paragraph, and only counts if its range names the paragraph:
    some paragraphs, such as exceptions, have no method. The Dutch and the English get the method's line at
    the same position, since the translation follows the Dutch line for line.
    """
    result, last = [], None
    for p in paragraphs:
        lines_nl = p["text_nl"].splitlines()
        own = [i for i, line in enumerate(lines_nl) if line.startswith(METHOD)]
        if own:
            i = own[-1]
            lines_en = p["text_en"].splitlines() if p["text_en"] else None
            last = (lines_nl[i], lines_en[i] if lines_en else None)
        elif last and p["paragraph"] in covered(last[0]):
            p = {
                **p,
                "text_nl": f"{p['text_nl']}\n{last[0]}",
                "text_en": f"{p['text_en']}\n{last[1]}" if p["text_en"] and last[1] else p["text_en"],
            }
        result.append(p)
    return result


def tidy_methods(paragraph: dict) -> dict:
    """Put a paragraph's inspection methods after its requirement, as one block, in Dutch and English alike.

    A requirement table puts each method next to the item it covers, so the rendered lines alternate between
    items and methods. The block keeps the first method's label; a later method that goes on with "–" loses
    it. A method of only "–", none, is left out.
    """
    lines_nl = paragraph["text_nl"].splitlines()
    lines_en = paragraph["text_en"].splitlines() if paragraph["text_en"] else [None] * len(lines_nl)
    body, methods = [], []
    for nl, en in zip(lines_nl, lines_en, strict=True):
        if not nl.startswith(METHOD):
            body.append((nl, en))
        elif nl[len(METHOD) :].strip() not in ("–", "-", "--"):
            if methods and nl[len(METHOD) :].lstrip().startswith("–"):
                nl, en = nl[len(METHOD) :].lstrip(), en and en.split(":", 1)[1].lstrip()
            methods.append((nl, en))
    lines = body + methods
    return {
        **paragraph,
        "text_nl": "\n".join(nl for nl, _ in lines),
        "text_en": "\n".join(en for _, en in lines) if paragraph["text_en"] else None,
    }


def reference(annex: str | None, article: str, paragraph: str | None = None) -> str:
    """Name an article or paragraph as the translation does: "Annex VIII, Article 30, paragraph 1".

    A definition is named by its article only: its paragraph is the term, which the text starts with.
    """
    parts = [f"Annex {annex}"] if annex else []
    parts.append(f"Article {article}")
    if paragraph and PARAGRAPH_NUMBER.fullmatch(paragraph):
        parts.append(f"paragraph {paragraph}")
    return ", ".join(parts)


def chunk_id(granularity: str, bwb_id: str, annex: str | None, article: str, paragraph: str | None = None) -> str:
    """Identify a chunk: "paragraph:BWBR0025798:5.2.47:2", "article:BWBR0025798:VIII.30"."""
    parts = [granularity, bwb_id, f"{annex}.{article}" if annex else article]
    return ":".join(parts + ([paragraph] if paragraph else []))


def vehicle_type(article: dict) -> str | None:
    """The vehicle type of a chapter 5 article: its division's heading, "Division 2 Passenger cars"."""
    if article["annex"] or article["chapter"] != "5" or article["section"] in GENERAL_DIVISIONS:
        return None
    division = next((h for h in article["headings_en"] or [] if h.startswith("Division ")), None)
    return re.sub(r"^Division \S+ ", "", division) if division else None


def is_definitions_intro(paragraph: dict) -> bool:
    """Whether a paragraph only opens a list of definitions: "In deze regeling wordt verstaan onder:"."""
    return paragraph["paragraph"] is None and paragraph["text_nl"].rstrip().endswith("verstaan onder:")


def chunks(article: dict) -> list[dict]:
    """The chunks of a translated row of regulation_articles: the article, then each of its paragraphs."""
    if not article["text_en"]:
        return []
    annex, nr = article["annex"], article["article"]
    # Below the title of the chapter or the annex, which says little about the chunk.
    place = (article["headings_en"] or [])[1 if annex is None else 2 :]

    def chunk(granularity: str, text_nl: str, text_en: str, paragraph: str | None) -> dict:
        return {
            "id": chunk_id(granularity, article["bwb_id"], annex, nr, paragraph),
            "granularity": granularity,
            **{k: article[k] for k in ("bwb_id", "version", "annex", "article")},
            "paragraph": paragraph,
            **{k: article[k] for k in ("chapter", "section")},
            "vehicle_type": vehicle_type(article),
            **{k: article[k] for k in ("headings", "headings_en", "effective_from")},
            "text_nl": text_nl,
            "text_en": text_en,
            "text": " › ".join([*place, reference(annex, nr, paragraph)]) + "\n" + text_en,
        }

    tidy = [tidy_methods(p) for p in article["paragraphs"]]
    paragraphs = [p for p in with_shared_methods(tidy) if p["text_en"] and not is_definitions_intro(p)]
    by_paragraph = [chunk("paragraph", p["text_nl"], p["text_en"], p["paragraph"]) for p in paragraphs]
    if len(article["text_en"]) > MAX_ARTICLE_CHARS:
        by_article = [{**c, "granularity": "article", "id": "article" + c["id"][len("paragraph") :]} for c in by_paragraph]
    else:
        by_article = [chunk("article", article["text_nl"], article["text_en"], None)]
    return by_article + by_paragraph


def main(env: str = "dev") -> None:
    """Rebuild the chunks table from the articles table, with an embedding of each chunk's text."""
    config = load_config(env=env)
    spark = spark_session()
    articles = spark.table(f"{config.full_schema_name}.{ARTICLES_TABLE}").collect()
    rows = [c for a in articles for c in chunks(a.asDict(recursive=True))]
    duplicates = [i for i, n in Counter(r["id"] for r in rows).items() if n > 1]
    if duplicates:
        raise ValueError(f"Chunk ids are not unique: {duplicates[:10]}")
    endpoint = config.embedding_endpoint.replace("'", "''")
    df = spark.createDataFrame(rows, SCHEMA).withColumn(
        "embedding", F.expr(f"CAST(ai_query('{endpoint}', text) AS ARRAY<FLOAT>)")
    )
    table = f"{config.full_schema_name}.{TABLE}"
    df.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(table)
    # The Lakebase synced table syncs the changes through the change data feed.
    spark.sql(f"ALTER TABLE {table} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
    for granularity, n in spark.table(table).groupBy("granularity").count().orderBy("granularity").collect():
        print(f"{table}: {n} {granularity} chunks")


if __name__ == "__main__":
    main(get_env())
