# Vehicle rules assistant

The course project of this repo: an agent that tells a car owner, buyer or garage which Dutch vehicle rules apply to a specific vehicle, what they mean in practice, and whether the APK (the mandatory periodic inspection) checks them. Its code is in `src/vehicle_rules/`.

Figures below were checked on 13 October 2026 against the version of the regulation in force since 1 July 2026.

## Goal

The rules are public but hard to use. They sit in one regulation, the *Regeling voertuigen*, of 1,210 articles of legal Dutch, 942 of which set requirements for vehicles in use. About a third of the requirements for passenger cars only apply under conditions such as a first-use date, fuel type, equipment, mass or use. These conditions are written into the legal sentences, often combined or as exceptions to other paragraphs, and are not stored as fields.

Seatbelts are an example (article 5.2.47):

| First used | Seatbelts required on |
|---|---|
| Before 1971 | No seats |
| 1971 to 1989 | The driver's seat, and the seat next to it if that seat is by a door |
| 1990 to September 2000 | All forward-facing seats |
| After September 2000 | All seats |

Owners also don't use the legal terms. "Tinted windows" has to be matched to *lichtdoorlatendheid van de voorruit en zijruiten* (light transmission of the windscreen and front side windows). That rule applies to every passenger car, but the APK doesn't check it (article 5.2.42, paragraph 3).

The agent finds the articles that apply, checks their conditions against the vehicle's data from the plate, asks the user for what the register doesn't hold (such as equipment), and explains the result in plain language.

## Why this needs an LLM

The arXiv example in the course uses an LLM to work through a large amount of free text. This project does the same: the answer is in the legal text, not in a table that a join can reach.

| Step | Done by |
|---|---|
| Download the regulation and split it into articles and paragraphs | Code (clean XML, no PDF parsing) |
| Pick the section for the vehicle type (car, van, motorcycle, ...) | Code, from the plate |
| Apply first-use date conditions | Mostly code: they follow a fixed phrasing (*in gebruik genomen na ...*) |
| Apply other conditions: fuel, equipment, mass, use, exceptions to other paragraphs | LLM |
| Match an owner's question to the right articles among about 940 | LLM and embeddings |
| Explain the rule and how it's inspected in plain language | LLM |
| Batch step: extract per paragraph what it applies to, plus an English summary | LLM over the whole text |

## Data

| Source | What | Size | Format | Licence |
|---|---|---|---|---|
| Regeling voertuigen (`BWBR0025798`) | Technical requirements per vehicle type and how they're inspected | 1,210 articles, 1.55M characters | XML from the official BWB repository | No copyright on laws and regulations (Auteurswet, article 11) |
| Registered vehicles (`m9d7-ebf2`) | Per plate: first-use date, vehicle type, EU category, body type, mass | 16.9M vehicles | RDW open data API | Public domain |
| Fuel (`8ys7-d773`) | Per plate: fuel type and emission class | One or more rows per vehicle | RDW open data API | Public domain |

How the regulation is built:

- **Chapter 5, *Permanente eisen*:** 942 articles and about 980k characters, split into sections by vehicle type. Passenger cars are section 2 (articles 5.2.x): 68 articles with 302 requirements.
- **Each requirement is a table row:** paragraph number | requirement | inspection method. In 104 places, the inspection method says the APK doesn't check the requirement.
- **Conditions:** at least 29% of the 3,719 requirements in chapter 5 are conditional. For passenger cars it's 37% of requirements, in 33 of the 68 articles. These are lower bounds from keyword counting, spot-checked by hand.
- **Annex VIII:** 174k characters that work out the chapter 5 requirements in detail. It is part of the same XML.
- **Versions:** the regulation has had 74 versions since 2009, most recently on 1 January and 1 July 2026.

The vehicle register doesn't record equipment (a tow bar, a catalytic converter, headlight levelling), so the agent has to ask the user about it.

Possible extensions: the two Dutch laws that chapter 5 links to most, in the same XML format.

- *RVV 1990*: the traffic rules, 103 links, 93k characters.
- *Kentekenreglement*: number-plate and registration rules, 19 links, 115k characters.

## How it maps to the course

- **Ingestion.** Download the XML of the version in force into a volume, split it into articles and paragraphs, and store them in Delta tables.
- **Batch LLM step.** For each paragraph, extract what it applies to as columns: vehicle type, first-use date range, fuel, equipment, mass, use, and whether the APK checks it. Also write an English summary to embed.
- **Retrieval.** pgvector in Lakebase over the summaries, combined with Dutch full-text search and filtered on the extracted conditions.
- **Agent.** Tools for the plate lookup and for article search and lookup. It asks follow-up questions about equipment, and conversation memory lives in Lakebase.
- **Evaluation.** See below.
- **Deployment and monitoring.** A serving endpoint with tracing, with plates masked in traces. Each new version of the regulation triggers a re-index, the evaluation runs again, and the answers are compared with the previous version's.

## A good answer

- **Correct.** It only cites articles that exist and apply to this vehicle type and date, with article number and version.
- **Clear.** It explains the rule, and how it's inspected, in plain language.
- **Safe.** It never says something is allowed without stating the conditions. When a question falls outside the regulation (traffic rules, tax, insurance), it says so instead of guessing.

## Evaluation

- **Retrieval.** Generate owner-style questions per article, so the correct article for each question is known and retrieval can be scored exactly.
- **Extraction.** Check the extracted first-use dates against a regex over the fixed phrasing.
- **Answers.** Judge answers against the source article for correctness, plain language and safety, plus a small hand-labelled set of real questions.

## Out of scope

- Legal advice: the agent explains and cites the text, it doesn't decide cases.
- EU and UNECE type-approval texts. Chapter 5 and Annex VIII hardly refer to them, and they matter mainly to manufacturers.
- Vehicle recalls and APK defect statistics. Both were considered as use cases and dropped, because they're mostly structured lookups.

## Open questions

1. **Embeddings for Dutch text.** The course's embedding service routes to GTE Large, which is English. The plan is to embed the English summaries and add Dutch full-text search, unless the gateway offers a multilingual model.
2. **Coverage.** How many real owner questions chapter 5 can answer. Test this with about 20 questions before building. Adding RVV 1990 is the first extension if traffic-rule questions come up often.
3. **No PDF parsing.** The source is clean XML, so the document-parsing exercise of the arXiv example is replaced by the batch extraction step.
