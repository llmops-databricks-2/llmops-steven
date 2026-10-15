# Week 1

The homework: apply the week's workflow to your own project, with the arXiv ingestion notebook as a
reference and no credentials in the pull request.

| # | Homework | Status |
|---|---|---|
| 01 | Choose the use case you want to build | Done: [use_case.md](use_case.md) |
| 02 | Develop preprocessing in VS Code and run it on Databricks | Done: `src/vehicle_rules/ingest.py`, developed in VS Code and run there through databricks-connect, runs on Databricks as the unscheduled `ingest` job on serverless (`resources/ingest.job.yml`, under 2 minutes) |
| 03 | Experiment with LLM calls through a model service | Open |
| 04 | Submit a pull request with your code and observations | Open: this document and the pull request from `week-1` |

## The English translation

The Regeling voertuigen has no English version, official or unofficial: the official repository
publishes it in Dutch only. The course's embedding model, GTE Large, is English-only, so for the
purposes of this course we translated it ourselves, once and "by hand" with Claude: article 2.2 and
all of chapter 5 (935 articles) of the version in force since 2026-07-01, following a fixed glossary.

- It is not an official translation, and the Dutch text prevails.
- The pipeline does not make or update it. It lives in the `en/` folder of the volume, and the ingest
  reads it from there into `text_en`. A new version of the regulation gets no English text until it
  is translated again.
- "(APK)" is added after every "periodic inspection", so that English questions about the APK find
  those lines.

## The RDW vehicle data

Two tables in `llmops_dev.steven`, both RDW open data:

- `rdw_registered_vehicles`: 9,738,055 passenger cars with 49 columns, data up to 2026-10-11. One
  row per car that is currently registered, not exported, insured and not a taxi.
- `rdw_registered_vehicles_fuel`: 11,445,182 rows, one per fuel per car, so a hybrid has two;
  609 cars have no fuel row.

For the rules, the date that counts is `first_admission_date`: article 2.2 defines "put into
service" as the date of first admission, which can be abroad, not the date of first registration
in the Netherlands (`first_registration_date_nl`).

How the data got here: we copied the tables as they are from the gold (cars) and silver (fuel)
layers of our separate RDW lakehouse, which downloads the RDW open data every week.

1. Exported both tables to Parquet in that workspace (serverless, under a minute). We kept only the
   fuel rows of the cars in the gold table and dropped three ingest-metadata columns.
2. Downloaded the Parquet files (690 MB) with `databricks fs cp` and uploaded them to
   `vehicle_rules_files/rdw/2026-10-15/` in the course volume.
3. Loaded them with
   `CREATE TABLE … AS SELECT * EXCEPT (_rescued_data) FROM read_files(…, format => 'parquet')`, and
   checked the row counts and distinct plates against the export.

This is not the most elegant approach: a manual one-off copy, with no lineage and no refresh, so the
snapshot ages. But the purpose of this course is not ETL, so we carried over the finished tables
without the logic behind them. For how the RDW open data is downloaded and processed (bronze and
silver layers with SCD2 history and data-quality checks, and the gold filters above), see the public
variant of the RDW project: [CauchyIO/rdw_blog_repo](https://github.com/CauchyIO/rdw_blog_repo).
