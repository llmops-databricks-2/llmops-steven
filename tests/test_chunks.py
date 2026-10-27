"""Paragraphs of the regulation's XML and the chunks made of them."""

import xml.etree.ElementTree as ET

from vehicle_rules.chunks import chunks
from vehicle_rules.ingest import paragraphs, split_english

REQUIREMENTS = """
<artikel>
  <kop><label>Artikel</label><nr>5.2.47</nr></kop>
  <table><tgroup cols="3"><thead><row>
    <entry/><entry><al>Eisen</al></entry><entry><al>Wijze van Keuren</al></entry>
  </row></thead><tbody>
    <row><entry><al>1.</al></entry><entry><al>Gordels voor alle zitplaatsen.</al></entry>
      <entry><al>Leden 1 en 2: visuele controle.</al></entry></row>
    <row><entry><al>2.</al></entry><entry><al>De gordels mogen:</al></entry><entry/></row>
    <row><entry/><entry><al>a. niet zijn beschadigd.</al></entry><entry/></row>
  </tbody></tgroup></table>
</artikel>
"""


def test_paragraphs_of_a_requirement_table() -> None:
    """A numbered row starts a paragraph; an unnumbered row, such as an item a., stays with it."""
    result = paragraphs(ET.fromstring(REQUIREMENTS), requirements=True)
    assert result == [
        ("1", ["1. Gordels voor alle zitplaatsen.", "Wijze van keuren: Leden 1 en 2: visuele controle."]),
        ("2", ["2. De gordels mogen:", "a. niet zijn beschadigd."]),
    ]


def test_english_is_split_like_the_dutch() -> None:
    """The English follows the Dutch line for line; when the lines differ, no paragraph gets English."""
    parts = [("1", ["1. a", "Wijze van keuren: b"]), ("2", ["2. c"])]
    assert split_english(parts, "1. A\nInspection method: B\n2. C") == ["1. A\nInspection method: B", "2. C"]
    assert split_english(parts, "1. A\n2. C") == [None, None]


ARTICLE = {
    "bwb_id": "BWBR0025798",
    "version": "2026-07-01_0",
    "annex": None,
    "article": "5.2.47",
    "chapter": "5",
    "section": "2",
    "headings": [],
    "headings_en": ["Chapter 5 Permanent requirements", "Division 2 Passenger cars", "§ 9 Bodywork"],
    "effective_from": None,
    "text_nl": "1. a\nWijze van keuren: Leden 1 en 2: x\n2. b",
    "text_en": "1. A\nInspection method: Paragraphs 1 and 2: X\n2. B",
    "paragraphs": [
        {"paragraph": "1", "text_nl": "1. a\nWijze van keuren: Leden 1 en 2: x", "text_en": "1. A\nInspection method: Paragraphs 1 and 2: X"},
        {"paragraph": "2", "text_nl": "2. b", "text_en": "2. B"},
    ],
}


def test_chunks_at_both_granularities() -> None:
    """An article chunk and one per paragraph, placed by their headings, with the vehicle type of the division."""
    result = chunks(ARTICLE)
    assert [c["id"] for c in result] == [
        "article:BWBR0025798:5.2.47",
        "paragraph:BWBR0025798:5.2.47:1",
        "paragraph:BWBR0025798:5.2.47:2",
    ]
    assert result[1]["text"].splitlines()[0] == "Division 2 Passenger cars › § 9 Bodywork › Article 5.2.47, paragraph 1"
    assert {c["vehicle_type"] for c in result} == {"Passenger cars"}


def test_paragraph_gets_the_method_shared_with_it() -> None:
    """Paragraph 2 has no inspection method of its own: "Leden 1 en 2" in paragraph 1 covers it."""
    assert chunks(ARTICLE)[2]["text_en"] == "2. B\nInspection method: Paragraphs 1 and 2: X"
