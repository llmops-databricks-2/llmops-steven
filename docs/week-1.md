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
