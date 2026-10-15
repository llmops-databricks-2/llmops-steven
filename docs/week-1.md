# Week 1

The homework: apply the week's workflow to your own project, with the arXiv ingestion notebook as a reference and no credentials in the pull request.

| # | Homework | Status |
|---|---|---|
| 01 | Choose the use case you want to build | Done: [use_case.md](use_case.md) |
| 02 | Develop preprocessing in VS Code and run it on Databricks | Done: `src/vehicle_rules/ingest.py`, developed in VS Code and run there through databricks-connect, runs on Databricks as the unscheduled `ingest` job on serverless (`resources/ingest.job.yml`, under 2 minutes) |
| 03 | Experiment with LLM calls through a model service | Done: `notebooks/week_1_model_service.py`, run in the workspace; preliminary findings below |
| 04 | Submit a pull request with your code and observations | Open: this document and pull request #1 |

## Where the data lives

All data is in the course workspace, in the dev schema `llmops_dev.steven`; none of it is in this repo, and the code reads only from the workspace. Source files are kept as they are in the volume `vehicle_rules_files`, and what the assistant queries is in managed Delta tables. Acc and prd hold no data yet.

| Where | What | Written by |
|---|---|---|
| Volume, `bwb/` | The regulation as official XML, one file per version: `BWBR0025798_2026-07-01_0.xml` (9 MB) | The `ingest` job, each run |
| Volume, `en/` | The English translation as markdown, `BWBR0025798_2026-07-01_0.en.md` (1 MB), and the `glossary.md` it follows | Uploaded once by hand |
| Volume, `rdw/2026-10-15/` | The RDW export as Parquet: `registered_vehicles/` and `registered_vehicles_fuel/` (690 MB) | Uploaded once by hand |
| Delta table `regulation_articles` | One row per article (1,479): its place in the regulation (annex, chapter, section, headings), `effective_from`, `text_nl` and, for the 935 translated articles, `text_en` | The `ingest` job, which overwrites it from `bwb/` and `en/` |
| Delta table `rdw_registered_vehicles` | One row per passenger car (9,738,055), 49 columns | Loaded once from `rdw/2026-10-15/` |
| Delta table `rdw_registered_vehicles_fuel` | One row per fuel per car (11,445,182), 36 columns | Loaded once from `rdw/2026-10-15/` |

## The English translation

The Regeling voertuigen has no English version, official or unofficial: the official repository publishes it in Dutch only. The course's embedding model, GTE Large, is English-only, so for the purposes of this course we translated it ourselves, once and "by hand" with Claude: article 2.2 and all of chapter 5 (935 articles) of the version in force since 2026-07-01, following a fixed glossary.

- It is not an official translation, and the Dutch text prevails.
- The pipeline does not make or update it. It lives in the `en/` folder of the volume, and the ingest reads it from there into `text_en`. A new version of the regulation gets no English text until it is translated again.
- "(APK)" is added after every "periodic inspection", so that English questions about the APK find those lines.

## The RDW vehicle data

Two tables in `llmops_dev.steven`, both RDW open data:

- `rdw_registered_vehicles`: 9,738,055 passenger cars with 49 columns, data up to 2026-10-11. One row per car that is currently registered, not exported, insured and not a taxi.
- `rdw_registered_vehicles_fuel`: 11,445,182 rows, one per fuel per car, so a hybrid has two; 609 cars have no fuel row.

For the rules, the date that counts is `first_admission_date`: article 2.2 defines "put into service" as the date of first admission, which can be abroad, not the date of first registration in the Netherlands (`first_registration_date_nl`).

How the data got here: we copied the tables as they are from the gold (cars) and silver (fuel) layers of our separate RDW lakehouse, which downloads the RDW open data every week.

1. Exported both tables to Parquet in that workspace (serverless, under a minute). We kept only the fuel rows of the cars in the gold table and dropped three ingest-metadata columns.
2. Downloaded the Parquet files (690 MB) with `databricks fs cp` and uploaded them to `vehicle_rules_files/rdw/2026-10-15/` in the course volume.
3. Loaded them with `CREATE TABLE … AS SELECT * EXCEPT (_rescued_data) FROM read_files(…, format => 'parquet')`, and checked the row counts and distinct plates against the export.

This is not the most elegant approach: a manual one-off copy, with no lineage and no refresh, so the snapshot ages. But the purpose of this course is not ETL, so we carried over the finished tables without the logic behind them. For how the RDW open data is downloaded and processed (bronze and silver layers with SCD2 history and data-quality checks, and the gold filters above), see the public variant of the RDW project: [CauchyIO/rdw_blog_repo](https://github.com/CauchyIO/rdw_blog_repo).

## Model service experiment: preliminary findings

`notebooks/week_1_model_service.py` calls `course_ops.gateway.chat` (GPT OSS 120B) through Unity Gateway as in the course's notebook 1.1: `WorkspaceClient` creates a 20-minute token for the OpenAI client on the gateway URL, using the Responses API. Articles come from `regulation_articles`, the car from `rdw_registered_vehicles`: a two-seat kit car without doors, put into service in 1987 and registered in the Netherlands in 2009. Each prompt ran once, so these are examples, not rates.

| # | Prompt | Result | Tokens in / out |
|---|---|---|---|
| 1 | Rear seatbelts on a 1987 car, no article given | Right conclusion, invented legal basis (nonexistent regulation, wrong article numbers); cut off | 154 / 1,024 |
| 2 | Same, with article 5.2.47 and the car's register data | Right paragraphs (3, 5, 6), but guessed the top speed the register leaves empty and ignored the 0 doors | 849 / 538 |
| 2b | Same, reasoning effort medium | Cut off: reasoning used most of the 1,024 tokens | 849 / 1,024 |
| 3 | Same, with the Dutch text | Right paragraphs, but invented doors and misquoted a date | 930 / 636 |
| 4 | Conditions of article 5.2.11 as JSON, strict schema | Valid JSON for all 12 paragraphs, dates and limits right | 1,452 / 991 |
| 5 | "Tinted windows, and does the APK check them?" with article 5.2.42 | Core right (at least 55%, not checked at the APK), padded with invented detail that contradicts the article; cut off | 416 / 1,024 |
| 6 | Follow-up to 2: registered in the Netherlands in 2009, with article 2.2 | Wrong: took the Dutch registration as the date put into service | 1,454 / 546 |

Eight calls, including one at 256 output tokens, used 6,953 input and 6,039 output tokens: about 0.07 DBU at notebook 1.1's rates, 1 to 7 seconds per call.

- Given the right article, the model mostly applies its conditions; without it, it cites sources that do not exist.
- It does not reliably use the register data, and gets the date that counts wrong even with article 2.2. Checks on dates, mass and fuel are better done in code, with the model interpreting and explaining.
- Its explanations mix the article with plausible inventions, so answers need grounding, citations and evaluation.
- Structured output with a JSON schema works through the gateway, so extracting the conditions of all articles is feasible as preprocessing.
- Limitations: answers are long and half the calls hit the output limit, which reasoning counts towards; the gateway reports no reasoning-token count; output contains narrow no-break spaces and non-breaking hyphens; students cannot read `system.ai_gateway.usage`.

So the hard part of the use case is not answering from a given article, but finding the articles that apply, using the register data correctly and staying within the source. The next test is a set of real owner questions without the article given.
