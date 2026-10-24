# COA Puroseka — Heroku Eco

Deployed frontend: [chat](https://coa-puroseka-chat-1a5098100731.herokuapp.com/) · [administration](https://coa-puroseka-chat-1a5098100731.herokuapp.com/admin/sources).

The migration is complete: the source dataset, fixed ontology, and application state are stored in the new schema of the existing external PostgreSQL database. Sign in with the previously configured email/password. Base and Enhanced responses, event comparison, daily evolution, Review mode, and durable sessions/metering have passed production checks. All three applications use Eco web dynos with no add-ons. Token enforcement remains off. The AWS application has not been changed or shut down.

Three small services demonstrate a fixed context ontology, semantic enrichment, and NL2SQL over the complete prepared Puroseka dataset. The UI uses Cloudscape components and AI chat patterns. The same `ministral-8b-latest` model supports Base, Enhanced, and A/B comparison, with tool activity, SQL evidence, and administrator Review mode. Ranking answers present the returned SQL rows and calculate score comparisons in code. This avoids a second model narration adding unsupported causes or reversing numeric comparisons; generated SQL still needs evidence review.

This repository is separate from the [original AWS application](https://github.com/02loveslollipop/coa-puroseka). It contains source code and hand-authored semantic examples only. Source records, generated ontology artifacts, translations, database dumps, credentials, and operational reports remain private and ignored.

## Services and data

| Service | Purpose | Process |
|---|---|---|
| Chat | Chat/admin UI, same-origin browser gateway, model calls, asynchronous jobs, reviews, token accounting | `services.chat.main:app` |
| Ontology | Fixed catalog, RDF, examples, source inspection, guarded read-only SQL | `services.ontology.main:app` |
| Login | Salted scrypt passwords, persistent opaque sessions, roles, login throttling | `services.login.main:app` |

All three use the existing external PostgreSQL database in a new `coa_puroseka` schema. Ontology introspection exposes only the 16 registered source tables; authentication, accounting, jobs, and ontology storage are excluded from the model's SQL allowlist. Prefer separate restricted database users when the existing provider supports them. A shared owner credential provides logical separation, not a database privilege boundary.

The dataset contains 180 events, 1,229 cards, 26 characters, 950 event-card associations, 815 gachas, 2,250 labels, and **all 1,117,619 available ranking observations**. Ranking records cover 142 events; missing histories for the remaining events are retained as coverage information, never invented or treated as zero. Original JSON and provenance are preserved. Five curated examples demonstrate comparisons and gap-aware daily evolution.

There is no enrichment agent or automatic ontology generation at runtime. The prepared `puroseka-1.1.0` release is imported once. Token metering persists in PostgreSQL; **enforcement remains off**, with 100,000 tokens/hour parked as the configurable allowance. The same email/password opens chat and administration; no Cognito access is required.

## Local setup and checks

Use Python 3.12+, Node 22+, and a disposable local PostgreSQL instance for migration tests.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
npm --prefix web ci
npm --prefix web run build
.venv/bin/python -m pytest -q
```

Populate the ignored `.env` locally. Never put credentials in shell arguments, Git, screenshots, or issue text. `MIGRATION_DATABASE_URL` selects the existing destination and runtime account. When that account cannot provision schemas, set `MIGRATION_ADMIN_DATABASE_URL` locally to an owner/admin connection to the same database. The importer then keeps schema ownership with the operator and grants runtime access without source-data writes. The admin connection is never sent to Heroku. `SOURCE_DATABASE_URL` selects a restored private source snapshot. `SOURCE_ARTIFACT_DIR` holds the prepared catalog, manifests, labels, SQL examples, and Turtle file. The repository does not distribute these generated artifacts.

Before a production import, take and restore-check a backup of the existing target database. Then run:

```bash
.venv/bin/python scripts/backup_target.py
.venv/bin/python scripts/migrate_postgres.py
```

The migration refuses an existing target schema. It copies all source rows into one new namespace in a transaction, retains primary/unique keys and indexes, recreates validated relationships, compares per-table SHA-256 fingerprints using a common byte ordering across database locales, checks source/target query parity, preserves metering/jobs/reviews, seeds the existing administrator with a fresh local hash, and invalidates old browser sessions. An error rolls the transaction back. Unrelated schemas are neither imported into nor reset.

Run the three-service check against a **local disposable** migrated database by setting `LOCAL_TEST_DATABASE_URL` in your shell environment:

```bash
.venv/bin/python scripts/verify_services.py
# Optional: two real model requests, one Base and one Enhanced.
.venv/bin/python scripts/verify_services.py --live-model
```

The check exercises real service HTTP calls, authentication, CSRF, private-table rejection, source inspection, timelines, SQL, session persistence, usage persistence, and interrupted jobs. The optional model check also verifies asynchronous replies and redacted Review mode traces. For the deployed applications, `scripts/verify_deployment.py --live-model --restart` checks real HTTPS APIs, both model modes, event comparisons, daily evolution, and session/meter persistence after restarting existing Eco processes.

## Eco-only deployment

`app.json` declares exactly one **Eco web dyno** per application and **no add-ons, release commands, postdeploy commands, workers, or one-off dynos**. The deployment script validates that manifest before creating anything. It uses Heroku app-setups to specify Eco sizing before the first web process starts. It never provisions a database, upgrades a plan, changes existing applications, or falls back to a different dyno class.

Authenticate the Heroku CLI, publish the audited source to `coa-puroseka-heroku`, and run:

```bash
.venv/bin/python scripts/deploy_heroku.py
```

The script creates personal apps for login, ontology, and chat, injects only each service's required configuration through the authenticated API, and verifies Eco formation and zero add-ons. The Mistral key goes only to chat. The administrator's plaintext password is used only for the workstation migration and is not placed in Heroku config vars. Heroku deployment state and URLs are saved in ignored `.deploy/heroku-services.json`.

Eco dynos sleep after inactivity and share the account's 1,000-hour monthly pool. Dependency services can also need time to wake; the UI reports temporary service unavailability and allows retry. There are no keep-alive jobs. Eco is intended for this small demo, not continuous availability. [Heroku Eco behavior](https://devcenter.heroku.com/articles/eco-dyno-hours).

The database must be reachable from the workstation for backup/import and from Heroku for runtime access. A deployed frontend with an unavailable database is not a completed migration. Keep AWS available until sign-in, both model modes, review, source counts, and persistence have passed on Heroku. Do not shut down AWS as part of this deployment.

See [migration design and acceptance sequence](docs/heroku-migration-plan.md). Upstream attribution and licenses are retained in [THIRD_PARTY.md](THIRD_PARTY.md).
