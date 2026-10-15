"""Download the configured regulations as in force today and load their articles into a table.

Each regulation's XML goes to bwb/ in the project volume, and each article to a row of the
regulation_articles table. Where the volume holds an English translation of that version, in
en/{bwb_id}_{version}.en.md, its articles fill text_en.

Run it in a workspace notebook, or from the repo root through databricks-connect:
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

from vehicle_rules.config import load_config

BWB_URL = "https://repository.officiele-overheidspublicaties.nl/bwb"
# Elements rendered by their parent (numbers, headings) or not at all (version history, table layout).
SKIPPED_TAGS = {"meta-data", "kop", "lidnr", "li.nr", "colspec"}
# Running text inside a paragraph; anything else starts a new line.
INLINE_TAGS = {"intref", "extref", "nadruk", "sup", "inf", "sub"}
REQUIREMENT_HEADER = ["eisen", "wijze van keuren"]
EN_HEADING = re.compile(r"^#+ Article (\S+)$")
TABLE = "regulation_articles"
SCHEMA = (
    "bwb_id string, version string, annex string, article string, chapter string, section string, "
    "headings array<string>, effective_from date, text_nl string, text_en string"
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
        lines = []
        for row in elem.iter("row"):
            cells = [" ".join(text_lines(entry)) for entry in row.findall("entry")]
            if [c.lower() for c in cells if c] == REQUIREMENT_HEADER:
                continue
            if requirements and len(cells) in (2, 3):
                *number, requirement, method = cells
                lines += join([" ".join([*number, requirement])])
                lines += [f"Wijze van keuren: {method}"] if method else []
            elif any(cells):
                lines.append(" | ".join(c for c in cells if c))
        return lines

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
            text = "\n".join(text_lines(elem, requirements))
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


def english_articles(md: str) -> dict[str, str]:
    """Split an English translation into articles: number → its lines, laid out like the Dutch text."""
    articles: dict[str, list[str]] = {}
    current = None
    for line in md.splitlines():
        if not line.strip():
            continue
        m = EN_HEADING.match(line)
        if m:
            current = m.group(1)
            articles[current] = []
        elif line.startswith("#"):
            current = None
        elif current:
            articles[current].append(line)
    return {nr: "\n".join(lines) for nr, lines in articles.items()}


def spark_session() -> SparkSession:
    """Return the notebook's Spark session in the workspace, or a serverless one through databricks-connect."""
    if session := SparkSession.getActiveSession():
        return session
    from databricks.connect import DatabricksSession

    return DatabricksSession.builder.serverless().getOrCreate()


def main() -> None:
    """Ingest the configured regulations into the project volume and the articles table."""
    config = load_config()
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
        for a in articles:
            effective_from = date.fromisoformat(a["effective_from"]) if a["effective_from"] else None
            text_en = None if a["annex"] else english.get(a["article"])
            rows.append(
                (
                    regulation.bwb_id,
                    version,
                    a["annex"],
                    a["article"],
                    a["chapter"],
                    a["section"],
                    a["headings"],
                    effective_from,
                    a["text"],
                    text_en,
                )
            )
        translated = sum(1 for a in articles if not a["annex"] and a["article"] in english)
        print(f"{regulation.title} ({name}): {len(articles)} articles, {translated} of them in English")
    table = f"{config.full_schema_name}.{TABLE}"
    spark = spark_session()
    spark.createDataFrame(rows, SCHEMA).write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(table)
    print(f"{table}: {len(rows)} rows")


if __name__ == "__main__":
    main()
