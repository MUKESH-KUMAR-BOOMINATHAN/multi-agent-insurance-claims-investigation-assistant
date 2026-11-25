# Changelog

All notable changes to this project are documented here, in the
[Keep a Changelog](https://keepachangelog.com) format.

## [Unreleased] - one-click demo, sample data, Back-button fix

### Added (front page and admin demo)
- **Demo-first front page.** Most visitors have no account, so the first page leads with the demo (three role cards) and tucks the sign-in form behind "Staff or customer with an account? Sign in". Without the demo the form is shown as before.
- **Read-only admin demo.** `POST /demo/start` and `/demo/switch` accept `admin`. The demo admin sees **fictional** people and a **fictional** activity log (the real accounts and audit log are never read), can try the "+ New account" form (validated like the real one, nothing stored), and can read claims but cannot file or decide them. The real admin's username is never consulted for demo requests. (`demo.py`, `main.py`)
- A demo bar that switches between all three roles.
- Responsive mobile layout (bottom tab bar, stacked claims table).
- 9 more tests (254 in total) and a 25-check browser script for the front page and admin tour.

### Fixed (front page and admin demo)
- Switching between the two dashboard roles kept the same component mounted, so the role-switch buttons stayed disabled and the old screen lingered. A role change now remounts the dashboard.

### Added
- **One-click public demo** (`DEMO_ENABLED`). `POST /demo/start` gives a visitor a short-lived signed token for a throwaway investigator or customer identity, and `POST /demo/switch` changes role inside the same sandbox. There is no account, no password and nothing to register. (`demo.py`, `auth.py`)
- **Private sandboxes.** A visitor's claims carry their session id (`claims.demo_session`, added by the automatic migration), are invisible to everyone else and to real staff, are deleted after 24 hours, and are **never written to the feedback table**, so a stranger cannot change the similar-claims evidence or the data the model is retrained on.
- **Demo limits.** 5 claims per visitor, 90 requests a minute, 12 new sessions an hour per address and 200 overall, and one shared daily AI allowance (60 Groq calls): after that, demo claims get the rules-based recommendation through a new `use_llm` switch in the pipeline. Reaching the claim cap returns a `429` with a contact link and the app shows a dialog pointing to the portfolio.
- **Demo visitors are refused** account creation, account changes, password resets and changes, the activity log, and decisions on sample data (server-side, not just hidden).
- **Sample data.** On its first start the API seeds sample claims (real dataset rows run through the real pipeline, marked reviewed, spread over three weeks) so the dashboards are not empty, plus hidden template claims copied into each new sandbox so the investigator view has something to review. All rows are built in memory and inserted in one transaction, so an interrupted seed leaves nothing behind. `scripts/seed_demo.py` re-seeds or removes them (`--reset`, `--purge`).
- **Cold-start screen** from the first frame: it explains that free hosting sleeps, shows a timer, and retries on its own for about two minutes instead of failing on the first request.
- `demo_enabled` in `/health`; the demo block in `/auth/config` appears only when the demo is on.
- 27 new tests (245 in total): sandbox isolation, the customer to investigator round trip, no feedback written, every limit, forged demo tokens, clean-up, and all-or-nothing seeding.

### Fixed
- **Browser Back/Forward left the app.** Screens were React state only, so the URL did not change when opening a claim or a form. Every screen change now goes through one hook (`useHashRoute`) that pushes a history entry, or replaces it after a form is submitted so Back skips the finished form. Covered by a browser script.
- A global `section` CSS rule drew a stray line inside the new sign-in card, and the global `h1` letter-spacing squeezed the card titles; both are scoped now.

### Changed
- `render.yaml` sets `DEMO_ENABLED=true`; every demo setting is documented in `.env.example`.
- The sign-in page leads with the one-click demo when it is enabled.
- The README gained "Try it live", demo rows in the roles, security and free-tier tables, and two more lessons.

## [Previous] - free hosting: Postgres (Neon) and stronger sign-in

### UI
- **Sign-in redesigned:** a two-panel layout (product summary + form), a show-password toggle, and the same layout for "choose your password" and "connecting / server waking up".
- **Claims list:** headline tiles that point at what needs attention (pending, high risk not yet reviewed), quick filters (pending, high risk, manual review, reviewed), search, colour-coded risk bars, and who submitted each claim.
- **Claim detail rebuilt:** a summary header (amount, status, submitter, dates), a fraud-score gauge with its signals, the similar-claims vote as a bar plus per-claim similarity bars, a claim-facts panel, the recommendation with a confidence meter, and the decision as choice cards instead of dropdowns.
- **New-claim form** grouped into policy, insured person, vehicle, incident and amounts, with the total computed.
- **Customer portal:** a progress tracker on every claim (filed, under review, decision).
- Settings shows which database is in use. Dollar amounts always use US digit grouping.
- All 15 README screenshots retaken from a Postgres-backed, sign-in-on run with data created through the real API.

### Added
- **Postgres support** via `DATABASE_URL` (built for Neon's free tier), using SQLAlchemy Core: one schema (`db/schema.py`) and one code path for SQLite (local, tests) and Postgres (production). Schema creation and additive migrations run automatically; existing SQLite databases keep their rows. (`db/engine.py`)
- **Backups.** `scripts/backup_db.py` (full JSON backup from either database) and `scripts/restore_db.py` (into an empty database only; also moves local SQLite data to Neon). `backups/` is git-ignored.
- **Change your own password** (`POST /auth/password`), from Settings or the customer portal.
- **Temporary passwords.** New accounts and organization resets must choose their own password at first sign-in; until then every other route answers 403.
- **Per-account lockout** after 5 consecutive failures (15 minutes), stored in the database so it survives restarts and free-tier sleep; unknown usernames are counted the same way. A reset unlocks.
- **Activity log** for the admin (`GET /audit`, People page): sign-ins, failures, lockouts, password changes/resets, account changes, claims filed and reviewed; never passwords, tokens or claim contents.
- Production refuses to start without `DATABASE_URL` (unless `DATA_DIR` explicitly points at a persistent disk), so data can never silently go to a wiped filesystem.
- The test suite runs against Postgres too (`TEST_DATABASE_URL`); 218 tests.

### Changed
- **README rewritten for visitors:** the operator runbook (step-by-step deployment, production checklist, troubleshooting, operations, full configuration and API error tables) was removed; added "What makes it different", "What the free-tier deployment leaves out", "What I learned building it" and a tech-stack table. Every setting remains documented in `.env.example`.
- `render.yaml` now targets the **free** plan with Neon for storage (no disk).
- `/health` reports which database is configured without querying it, so health checks don't keep a scale-to-zero database awake.

### Fixed
- **`render.yaml` build would have failed**: it ran `python -m retrieval.warmup`, a module removed with the embeddings index. The step is gone (the similarity index builds at startup in about 0.1 s).

## [0.3.0] - roles and customer portal

### Added
- **Three roles.** Admin (from environment variables), investigators and customers (stored in a new `users` table). Every route is role-checked; the role is re-read from the account on each request, so deactivation is immediate. (`auth.py`, `db/users_store.py`)
- **Customer portal.** Customers sign in, see the policy on file, file a claim by describing only the incident and amounts (the server merges the policy details and computes the total), and follow their claims with a plain status: under review, approved or not approved. The fraud score and AI reasoning are never sent to customers. (`POST/GET /my/claims`, `GET /my/policy`, `CustomerPortal.jsx`)
- **People page** for the organization: create customer accounts with their policy details, create investigator accounts (admin only), reset passwords, activate/deactivate. (`/users` routes, `People.jsx`)
- Claims record who submitted them and the customer-facing outcome (`submitted_by`, `outcome` columns, migrated in place); the claims list shows *Submitted by*.
- 23 role tests (197 in total) and a browser walk-through of admin -> investigator -> customer.

### Changed
- `POST /auth/login` now also returns the account (`user: {username, role, full_name}`); `GET /auth/me` added.
- Claim form fields are shared between the organization form, the customer form and the new-customer form (`claimFields.js`, `FieldGrid.jsx`).

## [0.2.0] - production hardening and deployment

### Added
- **Authentication.** Single operator account: scrypt-hashed password, short-lived signed JWT (HS256, issuer and algorithm pinned), token held in browser memory only. `POST /auth/login` (rate limited, generic errors, no username enumeration timing signal), `GET /auth/config`, and a bearer-token requirement on every `/claims` route. Fail closed: the server refuses to start in production without credentials and refuses `AUTH_DISABLED` there. `scripts/hash_password.py` generates the hash and a JWT secret. (`auth.py`)
- **Frontend login.** Login screen, sign-out button, automatic return to login on a 401, a "connecting / waking up" state with retry for hosts that sleep, and readable API errors.
- **Request protections.** Body-size limit (declared length and streaming), request ids, one structured access line per request (path only), security headers (HSTS in production, `nosniff`, `X-Frame-Options`, `no-store`), generic 500 responses with a request id, API docs disabled in production, explicit CORS allow-list (`*` refused in production). (`middleware.py`)
- **Structured JSON logging** with request ids in production. (`logging_setup.py`)
- **Deployment.** `render.yaml` (Render blueprint: build, start with proxy headers and one worker, health check, persistent disk, secrets prompted not stored), `frontend/vercel.json` (build settings, CSP and security headers), `frontend/.env.example`, a full README deployment guide with a production checklist.
- **Fraud-rate statistic in the UI.** "x of the 15 most similar past claims were confirmed fraud" is now persisted (`similar_summary` column, migrated in place) and shown above the evidence table, plus a "Matched on" column explaining why each claim is similar.
- `scripts/evaluate_retrieval.py` reproduces the retrieval-quality figures.
- Tests: 174 in total (up from 101), covering auth, middleware, logging, similarity, and the fixes below.

### Changed
- **Similar-claims retrieval rewritten.** Text embeddings (bge-small, LlamaIndex, ChromaDB) were replaced by an interpretable Gower similarity over the incident facts and claim amounts. Measured with leave-one-out on the 1,000 historical claims: the fraud vote of 5 neighbours had ROC-AUC 0.564 with embeddings and **0.705** with the structured similarity (**0.767** over 15 neighbours, which is what the statistic now uses; the investigator is shown the closest 5). It also explains each match, builds in about 0.1 s instead of about 20 minutes, and fits a 512 MB instance (measured about 200 MB RSS). (`retrieval/similarity.py`)
- **Dependencies slimmed.** Removed `chromadb`, `llama-index*`, `torch`, `transformers` and `sentence-transformers` (about 1 GB). This also clears the 5 `chromadb` advisories that had no fix. Added `PyJWT`; `numpy` is now declared explicitly.
- Feedback is re-loaded into the similarity index from SQLite at startup, so the index is a pure derived cache: losing an ephemeral disk or the index never loses data.
- SQLite databases run in WAL mode, and their location is configurable with `DATA_DIR`.
- `retrain_model.py` and the index share one data-cleaning function (`claims_data.py`).

### Fixed
- **Empty "Next steps".** Groq could return an approve recommendation with no next steps (seen in the UI). Empty next steps and key factors are now backfilled with safe defaults and flagged in `overrides`; when a policy rule reverses the model's decision, its now-contradictory next steps are replaced. Covered by tests that fail without the fix.
- A feedback submission that created the index (cold start) could index the same claim twice; it no longer does.
- The embedder's default batch size needed more than 2.5 GB and over 15 minutes for 1,000 claims (found while building the previous index); that code path no longer exists, replaced as above.

### Security
- `bandit` (0 issues over about 2,400 lines) and `ruff` with security rules are clean. SQL statements remain literal with bound parameters.

## 2026-11-22 - Groq, fallbacks and call limits

### Changed
- **LLM provider: Ollama (local `llama3.1`) replaced by Groq.** The `ollama` dependency is removed; calls go to `POST https://api.groq.com/openai/v1/chat/completions` (JSON mode, `max_completion_tokens`) through `httpx`. Model, endpoint and limits are configurable (`GROQ_MODEL`, `GROQ_API_URL`, ...). `GROQ_MODEL` defaults to `openai/gpt-oss-20b` (with `GROQ_REASONING_EFFORT=low`); the usable models depend on your Groq organisation, so list yours via `GET /openai/v1/models`. On HTTP 429 the `retry-after` header is honoured only if short (`GROQ_MAX_RETRY_WAIT_S`), otherwise the request falls back to rules at once.
- `retrain_model.py` is now gated: stratified holdout metrics, comparison against a fair original-data-only baseline (not the production model, which has seen most of the holdout and would make every retrain fail), archive of the previous model, atomic write, `models/model_meta.json`, and `--dry-run` / `--force` flags. It no longer overwrites the live model unconditionally.
- `GET /claims` is paginated (`limit`, `offset`) and includes `recommendation_source`.
- `POST /claims` request bodies are bounded (string lengths, numeric ranges) and reject unknown fields.
- `build_index.py` rebuilds the collection from scratch, so re-running it no longer duplicates every document.
- The feedback form no longer pre-selects "Not fraud" / "Agreed"; the investigator must choose explicitly.
- Frontend API base URL is configurable via `VITE_API_URL`, and API errors (422 detail lists, 429, network failure) are shown as readable messages.

### Added
- Fallbacks everywhere a step can fail: rules-based recommendation when Groq is unavailable, manual-review routing when the fraud model is missing or errors, empty-evidence continuation when the vector index is missing, and non-fatal index insertion on feedback. Degradations are returned in a `warnings` list and shown in the UI.
- Groq call limiting: per-minute and per-day budgets, retry cap, request timeout, `max_completion_tokens` cap, circuit breaker, and a response cache. Claims that cannot be scored never call Groq. (`resilience.py`, `agents/groq_client.py`)
- Per-client rate limit on `POST /claims` (`429` with `Retry-After`).
- `GET /health` with fraud-model status and non-secret Groq status (configured, circuit state, budget usage).
- Recommendation provenance: `source` (`groq` / `rules` / `rules_fallback`), `model`, `fallback_reason`, `cached` and an `overrides` audit list, shown in the UI.
- `config.py` (environment-driven settings), `.env.example`, shared `retrieval/text.py`.
- Automated tests covering guards, the Groq client, schema validation and policy, every fallback path, the HTTP contract, persistence and the retraining gate.
- README rewritten with architecture and sequence diagrams (Mermaid), screenshots, a fallback table and a limitations section. This changelog.

### Fixed
- **Prompt-injection path:** `fraud_node` echoed the client-supplied value of an unseen category into `fraud_signals`, which is placed in the LLM prompt. Signals now contain only the project's own text.
- **LLM output was trusted:** the model's JSON was accepted unvalidated and the business rules lived only in the prompt. The reply is now schema-validated and the rules (no approve on flagged claims, no unscorable approvals, deny only when flagged) are enforced in code.
- **Lost claims on LLM failure:** an unreachable LLM caused a 500 and the claim was never stored. The pipeline now always returns a recommendation and the claim is always persisted.
- **Duplicate feedback:** feedback could be submitted repeatedly, writing duplicate training rows and duplicate index documents. It is now accepted once per claim (`409` otherwise; unique index on `claim_id`).
- **Invalid feedback returned 500:** `decision` / `confirmed_fraud_reported` are now validated enums (`422`).
- **Index failure after feedback:** a failure inserting into the vector index no longer fails the request or leaves the claim stuck as "pending".
- **Silent empty retrieval:** a missing vector index produced "0 of 0 similar claims" with no error. It now raises internally and surfaces as a warning.
- **Feature-order fragility:** inference rows are re-indexed to the model's training column order, so API field order can no longer break scoring.
- **Import-time crash:** a missing or corrupt `model.pkl` no longer prevents the API from starting.
- **Text drift:** the embedding text was built by three diverging copies (index build, query, feedback insert); one shared function is used now.
- **SQLite connections** are always closed (and rolled back on error); a corrupt JSON cell no longer breaks the whole claim list; `claims.db` is migrated in place (`warnings_json`).
- Unhashable categorical values (for example a list) no longer crash `fraud_node`.

### Security
- **Dependency audit:** `pip-audit` over the lockfile found 123 advisories in 13 transitive packages (pillow, nltk, aiohttp, filelock, urllib3, oauthlib, certifi, setuptools, opentelemetry, multidict, langgraph-sdk, banks, chromadb). All but chromadb were fixed by upgrading `uv.lock` (including major bumps of pillow 13, filelock 4, multidict 7 and oauthlib 4); the test suite and a real embedding + Chroma retrieval were re-run afterwards. The remaining 5 `chromadb` advisories have no published fix and concern the Chroma server mode, which this project does not use (documented in the README).
- `bandit` and `ruff` are clean on the application code. SQL statements in `claims_store` are now literal constants with bound parameters.

### Removed
- `ollama` dependency and the local-LLM requirement.
- `data/claims.db` and `data/feedback.db` are no longer tracked by git (they were already in `.gitignore`); the files remain on disk.
