"""Download the configured regulations as in force today and load their articles into a table.

Each regulation's XML goes to bwb/ in the project volume, and each article to a row of the
regulation_articles table, with its paragraphs (see `paragraphs`) for chunking. Where the volume holds an English translation of that version, in
en/{bwb_id}_{version}.en.md, its articles fill text_en. That translation is not made here: it was
made by hand with Claude, for the purposes of the course, and covers one version and only the parts the
assistant needs (article 1.1, article 2.2, chapter 5, article 11.11 and Annexes II and VIII).

Run it as the ingest job (resources/ingest.job.yml), or from the repo root through databricks-connect:
`DATABRICKS_CONFIG_PROFILE=llmops uv run python -m vehicle_rules.ingest`.
"""

import io
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound
from pyspark.sql import SparkSession

from vehicle_rules.config import get_env, load_config

BWB_URL = "https://repository.officiele-overheidspublicaties.nl/bwb"
# Elements rendered by their parent (numbers, headings) or not at all (version history, table layout).
SKIPPED_TAGS = {"meta-data", "kop", "lidnr", "li.nr", "colspec"}
# Running text inside a paragraph; anything else starts a new line.
INLINE_TAGS = {"intref", "extref", "nadruk", "sup", "inf", "sub"}
REQUIREMENT_HEADER = ["eisen", "wijze van keuren"]
PARAGRAPH_NUMBER = re.compile(r"\d+[a-z]*\.?")
EN_HEADING = re.compile(r"^#+ (?:Annex (\S+), )?Article (\S+)$")
TABLE = "regulation_articles"
SCHEMA = (
    "bwb_id string, version string, annex string, article string, chapter string, section string, "
    "headings array<string>, headings_en array<string>, effective_from date, text_nl string, text_en string, "
    "paragraphs array<struct<paragraph: string, text_nl: string, text_en: string>>"
)


def fetch(url: str) -> bytes:
    """Download a file from the BWB repository."""
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def download(bwb_id: str, on: date) -> tuple[str, bytes]:
    """Download the version of a regulation in force on a date; return its label and XML."""
    manifest = ET.fromstring(fetch(f"{BWB_URL}/{bwb_id}/manifest.xml"))
    in_force = [
        e
        for e in manifest.findall("expression")
        if e.findtext("metadata/datum_inwerkingtreding") <= on.isoformat() <= e.findtext("metadata/einddatum")
    ]
    # A correction of a version covers the same period but was published later.
    version = max(in_force, key=lambda e: (e.findtext("metadata/zichtdatum_start"), e.get("label")))
    label = version.get("label")
    return label, fetch(f"{BWB_URL}/{bwb_id}/{label}/xml/{bwb_id}_{label}.xml")


def join(run: list[str]) -> list[str]:
    """Turn collected running text into a line, if there is any."""
    text = " ".join("".join(run).split())
    return [text] if text else []


def text_lines(elem: ET.Element, requirements: bool = False) -> list[str]:
    """Render an element as lines: each paragraph, list item and table row on a line of its own.

    In requirement tables (paragraph number | Eisen | Wijze van keuren), each requirement is followed by a
    "Wijze van keuren:" line with its inspection method.
    """
    if elem.tag in ("plaatje", "illustratie"):
        return ["[afbeelding]"]
    if elem.tag == "table":
        return [line for row in elem.iter("row") for line in row_lines(row, requirements)]

    lines, run = [], [elem.text or ""]
    for child in elem:
        if child.tag in INLINE_TAGS or (elem.tag == "al" and child.tag not in ("lijst", "table", "plaatje")):
            run.append("".join(child.itertext()))
        elif child.tag not in SKIPPED_TAGS:
            lines += join(run)
            run = []
            lines += text_lines(child, requirements)
        run.append(child.tail or "")
    lines += join(run)
    number = (elem.findtext("lidnr") or elem.findtext("li.nr") or "").strip()
    return [f"{number} {lines[0]}", *lines[1:]] if number and lines else lines


def row_lines(row: ET.Element, requirements: bool = False) -> list[str]:
    """Render a table row as a line; a requirement row as its requirement and its inspection method."""
    cells = [" ".join(text_lines(entry)) for entry in row.findall("entry")]
    if [c.lower() for c in cells if c] == REQUIREMENT_HEADER:
        return []
    if requirements and len(cells) in (2, 3):
        *number, requirement, method = cells
        return join([" ".join([*number, requirement])]) + ([f"Wijze van keuren: {method}"] if method else [])
    return [" | ".join(c for c in cells if c)] if any(cells) else []


def row_number(row: ET.Element) -> str | None:
    """Return the paragraph number in the first of a table row's three cells, such as "2" for "2.", if any."""
    entries = row.findall("entry")
    number = " ".join(text_lines(entries[0])) if len(entries) == 3 else ""
    return number.rstrip(".") if PARAGRAPH_NUMBER.fullmatch(number) else None


def slug(term: str) -> str:
    """Turn a defined term into an identifier: "aanhangwagen met een stijve dissel" → "aanhangwagen-met-een-stijve-dissel"."""
    return re.sub(r"\W+", "-", term.lower()).strip("-")


def paragraphs(elem: ET.Element, requirements: bool = False) -> list[tuple[str | None, list[str]]]:
    """Split an article into its paragraphs, as (number, lines); together their lines are the article's.

    A paragraph is a lid; a numbered row of a table of requirements, with the unnumbered rows below it (its
    items a., b., or more inspection methods), also where the table lacks its "Eisen | Wijze van keuren"
    header; an item of a numbered list that opens an annex article, which annexes use instead of leden; or a
    definition in a list of definitions, numbered by its term ("aanhangwagen"). What follows a paragraph,
    such as a table belonging to the article, joins it. What precedes the first, such as an introduction with
    its list of conditions, is a paragraph without a number.
    """
    result: list[tuple[str | None, list[str]]] = []

    def add(number: str | None, lines: list[str], new: bool = True) -> None:
        if not new and result:
            result[-1][1].extend(lines)
        else:
            result.append((number, lines))

    children = [child for child in elem if child.tag not in SKIPPED_TAGS]
    for i, child in enumerate(children):
        if child.tag == "lid":
            add((child.findtext("lidnr") or "").strip().rstrip(".") or None, text_lines(child, requirements))
        elif child.tag == "table" and (requirements or any(row_number(row) for row in child.iter("row"))):
            first = True
            for row in child.iter("row"):
                if lines := row_lines(row, requirements):
                    add(row_number(row), lines, new=first or row_number(row) is not None)
                    first = False
        elif child.tag == "lijst" and child.get("type") == "ongemarkeerd":
            for li in child.findall("li"):
                lines = text_lines(li, requirements)
                add(slug(lines[0].split(":")[0]) if lines else None, lines)
        elif child.tag == "lijst" and i == 0:
            for li in child.findall("li"):
                # An item such as "Tabel 2" belongs to the paragraph before it.
                number = (li.findtext("li.nr") or "").strip()
                new = not result or bool(PARAGRAPH_NUMBER.fullmatch(number))
                add(number.rstrip(".") or None, text_lines(li, requirements), new=new)
        else:
            add(None, text_lines(child, requirements), new=not result)
    return [(number, lines) for number, lines in result if lines]


def parse_articles(xml: bytes) -> list[dict]:
    """Split a regulation into articles, from its main text and its annexes; repealed articles are left out."""
    root = ET.fromstring(xml)
    articles: list[dict] = []

    def walk(
        elem: ET.Element, headings: list[str], annex: str | None, chapter: str | None, section: str | None
    ) -> None:
        """Collect the articles inside an element, keeping track of where they are."""
        kop = elem.find("kop")
        nr = (kop.findtext("nr") or "").strip() if kop is not None else ""
        # An article is an <artikel> in the main text, a <divisie> labelled Artikel in annexes.
        if elem.tag == "artikel" or (elem.tag == "divisie" and kop is not None and kop.findtext("label") == "Artikel"):
            requirements = any(
                [" ".join("".join(e.itertext()).split()).lower() for e in row.findall("entry")][-2:]
                == REQUIREMENT_HEADER
                for row in elem.iter("row")
            )
            parts = paragraphs(elem, requirements)
            text = "\n".join(line for _, lines in parts for line in lines)
            if text and not re.fullmatch(r"\[?Vervallen\b.{0,80}", text):
                articles.append(
                    {
                        "annex": annex,
                        "article": nr,
                        "chapter": chapter,
                        "section": section,
                        "headings": headings,
                        "effective_from": elem.get("inwerking"),
                        "text": text,
                        "paragraphs": parts,
                    }
                )
            return
        if elem.tag == "bijlage":
            annex, chapter, section = nr, None, None
        elif annex is None and elem.tag == "hoofdstuk":
            chapter, section = nr, None
        elif annex is None and elem.tag == "afdeling":
            section = nr
        if kop is not None:
            headings = [*headings, " ".join("".join(kop.itertext()).split())]
        for child in elem:
            if child.tag not in SKIPPED_TAGS:
                walk(child, headings, annex, chapter, section)

    walk(root, [], None, None, None)
    return articles


def english_articles(md: str) -> dict[tuple[str | None, str], tuple[list[str], str]]:
    """Split an English translation into articles: (annex, number) → its headings and its lines.

    The lines are laid out like the Dutch text. The headings are those above the article, like `headings`
    of the Dutch; markdown stops at six levels, so an article on level 6 also falls under the heading
    before it on that level.
    """
    articles: dict[tuple[str | None, str], tuple[list[str], list[str]]] = {}
    stack: list[tuple[int, str]] = []
    current = None
    for line in md.splitlines():
        if not line.strip():
            continue
        level = len(line) - len(line.lstrip("#"))
        m = EN_HEADING.match(line)
        if m:
            current = m.groups()
            articles[current] = ([h for lvl, h in stack if lvl < level or lvl == level == 6], [])
        elif level:
            stack = [(lvl, h) for lvl, h in stack if lvl < level] + [(level, line[level:].strip())]
            current = None
        elif current:
            articles[current][1].append(line)
    return {key: (headings, "\n".join(lines)) for key, (headings, lines) in articles.items()}


def split_english(parts: list[tuple[str | None, list[str]]], text_en: str | None) -> list[str | None]:
    """Split an article's English into its paragraphs, line for line like the Dutch.

    Without English, or when its lines don't match the Dutch, each paragraph gets None.
    """
    lines = text_en.splitlines() if text_en else []
    if len(lines) != sum(len(nl) for _, nl in parts):
        return [None] * len(parts)
    result, start = [], 0
    for _, nl in parts:
        result.append("\n".join(lines[start : start + len(nl)]))
        start += len(nl)
    return result


def spark_session() -> SparkSession:
    """Return the notebook's Spark session in the workspace, or a serverless one through databricks-connect."""
    if session := SparkSession.getActiveSession():
        return session
    from databricks.connect import DatabricksSession

    return DatabricksSession.builder.serverless().getOrCreate()


def main(env: str = "dev") -> None:
    """Ingest the configured regulations into the project volume and the articles table."""
    config = load_config(env=env)
    workspace = WorkspaceClient()
    volume = f"/Volumes/{config.catalog}/{config.schema}/{config.volume}"
    workspace.files.create_directory(f"{volume}/bwb")
    rows = []
    for regulation in config.regulations:
        version, xml = download(regulation.bwb_id, date.today())
        name = f"{regulation.bwb_id}_{version}"
        workspace.files.upload(f"{volume}/bwb/{name}.xml", io.BytesIO(xml), overwrite=True)
        try:
            md = workspace.files.download(f"{volume}/en/{name}.en.md").contents.read().decode("utf-8")
            english = english_articles(md)
        except NotFound:
            english = {}
        articles = parse_articles(xml)
        unaligned = []
        for a in articles:
            effective_from = date.fromisoformat(a["effective_from"]) if a["effective_from"] else None
            headings_en, text_en = english.get((a["annex"], a["article"]), (None, None))
            paragraphs_en = split_english(a["paragraphs"], text_en)
            if text_en and paragraphs_en[0] is None:
                unaligned.append(a["article"] if a["annex"] is None else f"{a['annex']}:{a['article']}")
            rows.append(
                (
                    regulation.bwb_id,
                    version,
                    a["annex"],
                    a["article"],
                    a["chapter"],
                    a["section"],
                    a["headings"],
                    headings_en,
                    effective_from,
                    a["text"],
                    text_en,
                    [
                        (number, "\n".join(nl), en)
                        for (number, nl), en in zip(a["paragraphs"], paragraphs_en, strict=True)
                    ],
                )
            )
        translated = sum(1 for a in articles if (a["annex"], a["article"]) in english)
        print(f"{regulation.title} ({name}): {len(articles)} articles, {translated} of them in English")
        if unaligned:
            print(f"English not split into paragraphs, its lines differ from the Dutch: {', '.join(unaligned)}")
    table = f"{config.full_schema_name}.{TABLE}"
    spark = spark_session()
    spark.createDataFrame(rows, SCHEMA).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(table)
    print(f"{table}: {len(rows)} rows")


if __name__ == "__main__":
    main(get_env())
