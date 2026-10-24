# COA implementation — slide outline

Use these six slides after the existing problem slide. The figures match the prepared `puroseka-1.1.0` catalog.

## Slide 1 — Data used in the PoC

**Suggested layout:** A source table, with the ranking history highlighted.

| Source | Content | Records |
|---|---|---:|
| `events.json` | Event metadata and competition dates | 180 |
| `cards.json` | Card definitions, attributes and character references | 1,229 |
| `chracters.json` | Character metadata; spelling follows the original filename | 26 |
| `eventCards.json` | Explicit event–card associations | 950 |
| `gachas.json` | Banners, pools, pickups and draw configurations | 815 |
| Ranking snapshots | Scores by event, rank and observation time | 1,117,619 |

**Footer:** 3,894 ranking files processed; 142 of 180 events have valid observations. The other 38 have no valid observations in the supplied data.

## Slide 2 — How this COA is organized

**Suggested layout:** Three columns following **Scan → Model → Serve**.

| Element | Implemented behavior |
|---|---|
| **Scan: prepare the data** | Read JSON sources, normalize nested collections, preserve original records and source provenance. |
| **Model: define semantic context** | Describe entities, columns, relationships, aliases and interpretation rules; prepare English labels and SQL examples. |
| **Serve: answer questions** | Supply context to Mistral, generate SQL, validate and execute it, then summarize returned evidence. |

**Supporting figures:** 16 tables = 14 metadata tables + ranking observations + entity labels.

**Footer:** Fixed ontology `puroseka-1.1.0`, prepared once. There is no runtime enrichment agent or automatic ontology modification.

**Presenter note:** This is a small adaptation inspired by AWS Context Ontology Accelerator, with an independent PoC backend. It does not deploy the full upstream platform. The one-time label preparation produces approximate English translations; source-supplied English character names retain their source provenance.

## Slide 3 — What the ontology contains

**Suggested layout:** A small relationship diagram alongside this table.

| Part | Example in our ontology |
|---|---|
| **Classes** | `Event`, `Card`, `Character`, `RankingObservation`, `Gacha` |
| **Properties** | Event start time, card attribute, ranking score and observation timestamp |
| **Relationships** | A card depicts a character; a ranking observation belongs to an event |
| **Aliases** | “Competition” → Event; “ranking history” → RankingObservation |
| **Interpretation rules** | Competition ends at `aggregate_at`; missing observations are not zero scores |
| **SQL mappings** | Concepts map to physical tables, columns and join conditions |

**Suggested diagram:**

```text
RankingObservation ── observedInEvent ──► Event

EventCardAssociation ── associatedEvent ──► Event
EventCardAssociation ── associatedCard ───► Card ── depicts ──► Character
```

**Footer:** 15 validated relationships and 15 interpretation rules. The ontology is available as JSON semantic context and RDF/Turtle export.

**Presenter note:** Runtime inference receives JSON semantic context and queries PostgreSQL. The RDF/Turtle export represents the ontology; the PoC does not use an RDF database or a runtime ontology reasoner.

## Slide 4 — Concrete column semantics

**Suggested layout:** Show how a database type gains domain meaning.

The column values below are illustrative examples, not exported source records. Dataset counts elsewhere in this outline describe aggregate coverage.

| Column | Example | Meaning supplied by the semantic layer |
|---|---|---|
| `events.start_at` | `1704067200000` | Illustrative epoch **milliseconds**: 1 January 2024, 00:00 UTC. Convert using `to_timestamp(start_at / 1000.0)`. |
| `events.aggregate_at` | `1704758400000` | Illustrative competition boundary: 9 January 2024, 00:00 UTC. `closed_at` represents a later lifecycle boundary. |
| `cards.attr` | `cool` | A game attribute category. |
| `cards.card_rarity_type` | `rarity_1` | A one-star card category; birthday rarity is a separate category. |
| `ranking_observations.rank` | `100` | Leaderboard position. The player occupying that position can change over time. |
| `ranking_observations.observed_at` | PostgreSQL `timestamptz` | Already a timestamp; do not apply the epoch-millisecond conversion. |

**Callout:** `score` is the cumulative score observed at a ranking position. Daily gains must be calculated from selected observations.

**Presenter note:** An event–card association alone does not establish a positive bonus or pickup status. Generated English labels are approximate convenience translations, not official localization.

## Slide 5 — From natural language to SQL

**Suggested layout:** An execution flow above a comparison of the two context configurations.

```text
Question + context → Mistral generates SQL → SQL validation
→ Read-only PostgreSQL query → Returned rows → Mistral answer
```

| No enhancement | Enhanced |
|---|---|
| Physical database schema | Schema + ontology definitions + relationships + rules + coverage + matching labels + six NL2SQL examples |

**Example question:**

> Show the daily score evolution at rank 100 in event 1.

The supplied SQL example teaches the model to:

1. Filter observations to the competition window.
2. Select the latest observation for each UTC day.
3. Use `LAG()` to calculate changes between observed daily endpoints.
4. Return elapsed-day gaps so missing days remain explicit.

**Footer:** Both variants use `ministral-8b-latest`. A/B mode compares the same question with empty histories; inspect SQL and evidence when assessing the answers.

**Presenter note:** Other curated examples cover last observed competition scores, progression aligned by elapsed event day, event-associated characters, and distinct cards in gacha pools. UTC calendar days and elapsed 24-hour event days are different alignments. Providing these examples does not guarantee that every model-generated query will follow them correctly.

## Slide 6 — AWS implementation and interfaces

**Suggested layout:** A deployment diagram with four implementation notes.

```text
Cloudscape chat / COA administration
                  ↓
API Gateway → VPC Link → Internal ALB → ECS Fargate / FastAPI
                                            ├── RDS PostgreSQL
                                            └── Mistral sandbox API
```

- **Cognito:** Shared predefined login for chat and administration.
- **CloudFormation:** Application, authentication and gateway infrastructure.
- **Query controls:** SELECT-only access, SQL validation, execution timeout and bounded results.
- **Observability:** Tool activity before answers; Review mode exposes call JSON, executed SQL and returned rows.

**Footer:** Token usage is metered; enforcement is currently off.

**Presenter note:** The application runs in AWS Academy Learner Lab. Mistral is an external sandbox API. Supporting AWS services include ECR for application images, S3 for prepared artifacts, Secrets Manager for runtime credentials, and CloudWatch for logs. The fixed semantic context is reused at inference time.

## Supporting visuals and implementation references

Use screenshots of the ontology explorer and an A/B response as supporting visuals.

- [Public implementation repository](https://github.com/02loveslollipop/coa-puroseka)
- [Ontology definitions](../services/ontology/semantic/definitions.json)
- [Ranking semantics](../services/ontology/semantic/rankings.json)
- [Six curated NL2SQL examples](../services/ontology/semantic/few_shots.json)
- [User chat and implementation details](user-chat.md)

Source datasets and generated artifacts remain outside the public repository. This outline includes schema descriptions, aggregate counts and illustrative column examples for presentation purposes.
