# Week 2

The homework: prepare the documents for retrieval (inspect them, chunk them, embed them), serve the chunks from a Lakebase synced table, and compare semantic, filtered and hybrid search on the same questions. The plan is in [week-2-plan.md](week-2-plan.md).

| # | Homework | Status |
|---|---|---|
| 00 | Not course homework: complete the English translation | Done: see below |
| 01 | Inspect the processed documents and chunk tables; compare chunks with their source and note lost structure | To do |
| 02 | Review chunk sizes, try one alternative strategy, check the embedding model's input limit | To do |
| 03 | Create a Lakebase synced table; confirm primary key, vector type, sync state and row count | To do |
| 04 | Run semantic, filtered and hybrid search on the same questions; note what changes | To do |
| 05 | Submit through the pull-request process: configuration approach, observations, limitations | Open: this document and pull request #2 |

## The English translation, extended

The translation of week 1 covered article 2.2 and chapter 5: enough to find the rule that applies to a vehicle, but not enough to answer from it. Counting the references in the XML showed that chapter 5 leans on four parts that were still Dutch only:

| Part | Size | Why the assistant needs it |
|---|---|---|
| Annex VIII, "further detail of the permanent requirements and usage requirements" | 169 articles | Chapter 5 cites it 388 times, and article 5.2.0 says every passenger-car requirement is assessed with it where applicable. It holds the actual limits: rust damage, the windscreen damage zones, the emission tests, braking performance, lamps |
| Article 1.1, definitions | 157 definitions | The terms the conditions depend on (passenger car, motor caravan, the mass terms the register fields map onto) |
| Article 11.11 | 1 article | What applies when the register lacks the data an article needs |
| Annex II | 11 articles | How the date of first admission is set, which article 2.2 refers to |

They were translated on 26 October the same way as chapter 5: by hand with Claude subagents, one batch each, following the same glossary (`glossary.md` in the volume's `en/`), with no Databricks endpoint or LLM involved. The translation stays unofficial, and the Dutch text prevails.

- **Article 1.1.** Each definition starts with the English term followed by the Dutch term in brackets, "passenger car (personenauto): …", so a question in either language finds it. This is the second addition to the source text, after "(APK)".
- **Checks.** A script compares each batch with its Dutch source line by line: the same articles, the same lines, the same numbers, dates and references. The definitions and the windscreen articles (Annex VIII, Articles 91–94) were also compared by hand.
- **Consistency.** Joining the parts settled a few terms across the whole text, recorded in the glossary: "mass of the unladen vehicle" for massa ledig voertuig, "steered axle" and "forced-steered axle" as article 1.1 defines them (three chapter 5 articles were corrected), and "wheel guard" for wielafscherming.
- **Ingest.** `english_articles` in `ingest.py` now also reads "Annex VIII, Article 30" headings, and matches the English to a row by annex and article number, so annex rows get English too.

After the `ingest` job was rerun, 1,117 of the 1,479 rows in `regulation_articles` have `text_en`: 937 in the main text, 169 in Annex VIII and 11 in Annex II. The other 362 are the parts outside the use case, such as approvals, exemptions and the remaining annexes.

**Known gap.** Annex VIII ends with five appendices that are tables only: the rust damage grades per component (annexes 1–3), the load index (annex 4) and the variation in axle loads (annex 5). They are not articles, so `parse_articles` skips them, and they are in `regulation_articles` neither in Dutch nor in English. The rust rules can therefore only be answered in part: Annex VIII, Article 5 allows at most 2E of rust damage per component of a car with a self-supporting body, and Article 17 likewise for vehicles without one, but the grade of each component comes from annex 1 or annex 3.
