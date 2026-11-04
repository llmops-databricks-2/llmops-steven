# Week 2

The homework: prepare the documents for retrieval (inspect them, chunk them, embed them), serve the chunks from a Lakebase synced table, and compare semantic, filtered and hybrid search on the same questions.

| # | Homework | Status |
|---|---|---|
| 00 | Not course homework: complete the English translation | Done: see below |
| 01 | Inspect the processed documents and chunk tables; compare chunks with their source and note lost structure | Done: `regulation_chunks`, see below |
| 02 | Review chunk sizes, try one alternative strategy, check the embedding model's input limit | Done: see below |
| 03 | Create a Lakebase synced table; confirm primary key, vector type, sync state and row count | Done: `regulation_chunks_synced`, see below |
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

## Chunks at two granularities

The course's chunking does not carry over: it takes each element that `ai_parse_document` finds in a PDF. The Regeling voertuigen is XML with its structure marked up, so the question is not where to cut the text but which unit of the law to retrieve: the article or its paragraph. We keep both, in one table, to compare them before choosing.

- **`ingest.py`** also splits each article into its paragraphs, in a new `paragraphs` column of `regulation_articles`, from the XML: a lid; a numbered row of a requirement table, with its unnumbered rows (items a., b.); a numbered item of an annex article; or a definition, numbered by its term. Joined, the paragraphs are exactly `text_nl`. The English is split at the same lines, which works for all 1,117 translated articles. `headings_en` holds the English headings above each article.
- **`chunks.py`** builds `regulation_chunks`: per translated article, one chunk with `granularity = 'article'` and one per paragraph with `granularity = 'paragraph'`. `text` starts with a line placing the chunk ("Division 2 Passenger cars › § 9 Bodywork › Article 5.2.47, paragraph 2"), followed by the English, and is embedded with `databricks-gte-large-en` through `ai_query`. `vehicle_type` holds the vehicle type of a chapter 5 division, to filter on in item 04. The change data feed is on, for the synced table of item 03, and the chunks are merged into the table on `id` rather than overwriting it, so the feed holds only the chunks that changed.
- **The job.** `chunks` is a second task of the `ingest` job. A chunk keeps its embedding while its text stays the same, so a run embeds only new and changed chunks; embedding all of them takes about 0.5M tokens.
- **Inspection.** `notebooks/week_2_chunks.py` makes the counts and comparisons below.

| Granularity | Chunks | Median | p99 | Largest |
|---|---|---|---|---|
| Article | 1,273 | 649 characters, ~150 tokens | 4,814 characters, ~1,100 tokens | 9,253 characters, 2,203 tokens (Annex VIII, Article 1) |
| Paragraph | 3,735 | 365 characters, ~85 tokens | 1,759 characters, ~400 tokens | 7,011 characters, 1,752 tokens (Annex VIII, Article 1, paragraph 3) |

### Lost structure

Comparing the paragraph chunks with the XML:

- **Shared inspection methods.** A requirement table often states one method for several paragraphs: in article 5.2.47, "Leden 1 en 2: visuele controle" sits in the row of paragraph 1 only. The method says whether the APK checks a requirement, so `chunks.py` adds it to the paragraphs its range names ("Leden 1 en 2", "Leden 6 tot en met 9"): 926 paragraph chunks. It goes by the range, not by position, because some paragraphs, such as exceptions, have no method. Within a paragraph, the methods are put after the requirement, as in the table's own column.
- **References between paragraphs.** 498 paragraph chunks refer to another paragraph, such as "Paragraphs 1, 2 and 3 do not apply to folding seats" (5.2.47, paragraph 4). The paragraph that sets a rule does not hold its exceptions, so the agent has to read the whole article before it answers.
- **Figures.** 64 paragraph chunks hold an `[image]` placeholder, such as the windscreen areas of Annex VIII, Article 92.
- **Other tables.** 39 paragraph chunks hold a table flattened into cells separated by `|`, without its header.
- **Paragraphs without a number.** 320 paragraph chunks are an article without numbered paragraphs, or the introduction before them.
- **The appendices of Annex VIII**, the rust grades per component, are not in the chunks; see the known gap above.

### The embedding model's input limit

`databricks-gte-large-en` serves GTE Large (En) v1.5: 1024 dimensions and at most 8192 tokens. Longer input is cut at 8192 tokens without an error, so the end of a long text would be lost unnoticed. English legal text runs at about 4.4 characters a token. Only article 1.1, with its 157 definitions, comes near the limit as a whole: 8,110 tokens, 82 short. It is chunked per definition at both granularities instead, and the largest chunk is now 2,203 tokens.

### Article or paragraph: a first comparison

Seven owner questions, with the articles that answer them picked by hand, searched by cosine similarity at each granularity, in pandas on the table's vectors. The rank is that of the best-ranked article that answers, counting articles rather than chunks. Seven questions are a sanity check, not an evaluation.

| Question | Article | Paragraph |
|---|---|---|
| Tinted front side windows, and does the APK check them? | 1 | 1 |
| A car from 1987: seat belts in the back? | 1 | 1 |
| Rust damage on a car with a self-supporting body | 1 | 1 |
| Date of first admission of an imported car | 1 | 1 |
| Windscreen damage in the driver's field of view | 2 | 1 |
| Maximum noise level of a passenger car | 1 | 2 |
| What the APK checks on the exhaust of a petrol car | 1 | 5 |

- Both granularities find the article for most questions. Paragraphs do better on the windscreen and worse on the exhaust question, where paragraphs about the particulate filter (Annex VIII, Articles 45e to 45j) crowd out the carbon monoxide test of Articles 40 to 43.
- Near the top at both granularities is often the same requirement for another vehicle type: 5.5.42 and 5.3.42 after 5.2.42 for tinted windows. Filtering on `vehicle_type` (item 04) should remove them.

No granularity wins yet. Items 03 and 04 compare them on the synced table, with filtered and hybrid search.

## The synced table

The course offers two ways to serve the chunks: an AI Search endpoint with an index, or a Lakebase synced table queried through Postgres. It recommends Lakebase, and so do we, for its cost: an AI Search endpoint bills for as long as it exists, queried or not, while the Lakebase compute of the project (0.5 to 2 CU) suspends after five idle minutes. Storage, syncs, query embeddings and active queries still cost, and an open connection keeps the compute awake.

- **The configuration.** `project_config.yml` names the Postgres database, `vehicle_rules` on the project's production branch, and the synced table, `regulation_chunks_synced`. `notebooks/week_2_synced_table.py` creates the database and the pgvector extension if they are missing, then the synced table: `id` as primary key, triggered rather than continuous, the usage policy on its pipeline, and `embedding` overridden to `vector(1024)`, since a Delta array otherwise arrives as JSONB. Rerun, it starts a sync and waits for that update.
- **Delta stays the source.** Chunks change in `regulation_chunks` and reach Postgres through a sync, never by writing to the copy.
- **Merging, not overwriting.** A triggered sync reads the change data feed. Overwriting `regulation_chunks` on every run, with `overwriteSchema`, put every chunk in the feed as deleted and inserted again, and could break incremental syncs. The course appends instead. `chunks.py` now merges on `id`: it inserts new chunks, updates changed ones, deletes those that are gone, and embeds only new and changed texts. A change to the table's columns now means dropping it and recreating the synced table.
- **Not used.** The `ai_search()` SQL function over Lakebase is in Beta, needs Lakebase Search enabled for the project, which cannot be undone, and failed in the lecture. Item 04 queries Postgres directly.
- **The result.** `notebooks/week_2_synced_table.py` ran on the new workspace (below). It created the `vehicle_rules` database, owned by the role of whoever creates it, since the API requires an owner, and the synced table, whose state is `SYNCED_TABLE_ONLINE_NO_PENDING_UPDATE`. The notebook stops if the primary key is not `id`, if `embedding` is not `vector(1024)`, or if Postgres does not hold every chunk with its vector: 5,008 chunks, 1,273 articles and 3,735 paragraphs. The pipeline is triggered, so it is idle between syncs.

## The move to the new workspace

The course moved to a new workspace (`dbc-e81ca4d2-f6bd`) in November 2026, with a new SQL warehouse (`fd4639f83ff71fb7`). `week-2` took the host, warehouse and `embedding_service` from the migration pull request, and `llmops-new` is the CLI profile for the new workspace.

- **The data moved with it.** The course team copied the volume and the tables of `llmops_dev.steven`. The counts match this document and `docs/week-1.md`: 1,479 articles, 1,117 translated, and the 9,738,055 cars and 11,445,182 fuel rows, in the tables and in the Parquet files. The chunks kept their embeddings, so nothing was embedded again. This compares counts, not contents: the old workspace could no longer be reached to compare them with.
- **The change data feed was lost.** `regulation_chunks` was recreated with `CREATE TABLE AS SELECT`, which dropped `delta.enableChangeDataFeed`, and the `chunks` task does not set it on a table that already exists. It was set again with `ALTER TABLE` before the sync.
- **Redeployed.** The bundle state pointed at jobs of the old workspace, so the jobs were created anew under dev. Acc and prd hold no data and were not deployed.
