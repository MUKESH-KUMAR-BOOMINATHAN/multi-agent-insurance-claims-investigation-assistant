# Multi-Agent Insurance Claims Investigation Assistant

An end-to-end fraud investigation system that combines a trained ML classifier, RAG-based retrieval over historical claims, an LLM reasoning layer, and a human-in-the-loop feedback system — all orchestrated with LangGraph and exposed through a FastAPI backend and React dashboard.

Given a new insurance claim, the system scores its fraud risk, retrieves similar historical claims as supporting evidence, generates a structured investigation recommendation, and lets a human investigator confirm or override the outcome — with that feedback improving both future retrieval and the underlying model.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [How It Works](#how-it-works)
- [Setup & Installation](#setup--installation)
- [Running the App](#running-the-app)
- [API Reference](#api-reference)
- [The Feedback Loop](#the-feedback-loop)
- [Known Limitations & Future Work](#known-limitations--future-work)

---

## Overview

Manual insurance fraud investigation is slow and inconsistent — investigators must independently assess risk signals, recall similar past cases, and document their reasoning. This project automates the first pass of that workflow while keeping a human firmly in the loop for the final decision.

**The pipeline for every submitted claim:**

1. A trained **Random Forest classifier** scores the claim's fraud probability.
2. A **RAG retrieval layer** (ChromaDB + sentence embeddings) surfaces the 5 most similar historical claims and their confirmed outcomes.
3. A **local LLM** (Llama 3.1 via Ollama) synthesizes the score and the retrieved evidence into a structured recommendation with reasoning.
4. A **human investigator** reviews the recommendation, confirms or overrides it, and their decision is captured.
5. That feedback **immediately improves retrieval** (added to the vector index) and **can retrain the classifier** on demand.

---

## Architecture

```
                        ┌─────────────────┐
   New Claim  ────────► │   fraud_node     │  Random Forest classifier
                        │  (agents/)       │  scores fraud probability
                        └────────┬─────────┘
                                 │ status == "ok"?
                    ┌────────────┴────────────┐
                    │ yes                     │ no
                    ▼                         │
         ┌──────────────────┐                 │
         │  retrieval_node   │  ChromaDB RAG   │
         │  (retrieval/)      │  similar claims │
         └────────┬──────────┘                 │
                    │                          │
                    └────────────┬─────────────┘
                                 ▼
                    ┌─────────────────────┐
                    │ recommendation_node  │  Llama 3.1 (Ollama)
                    │   (agents/)          │  structured JSON verdict
                    └──────────┬───────────┘
                                 │
                                 ▼
                    ┌─────────────────────┐
                    │   Human Investigator  │  reviews via React dashboard
                    │   (feedback_node)      │  confirms / overrides outcome
                    └──────────┬───────────┘
                                 │
                  ┌─────────────┴─────────────┐
                  ▼                            ▼
      Chroma index (live insert)     feedback.db (SQLite)
      improves future retrieval             │
                                             ▼
                                  retrain_model.py (manual trigger)
                                  refits Random Forest on
                                  original data + feedback
```

The pipeline is orchestrated as a **LangGraph** `StateGraph` (`graph.py`), with conditional routing that skips retrieval entirely if the fraud model can't confidently score a claim (e.g. an unrecognized category or missing field) — those claims route straight to a forced "investigate" recommendation instead.

---

## Tech Stack

| Layer | Technology |
|---|---|
| ML Model | scikit-learn `RandomForestClassifier` |
| Orchestration | LangGraph |
| Retrieval / RAG | LlamaIndex + ChromaDB + `BAAI/bge-small-en-v1.5` embeddings |
| LLM Reasoning | Llama 3.1 (local, via Ollama) |
| Backend API | FastAPI |
| Persistence | SQLite (claims store + feedback store) |
| Frontend | React (Vite) |
| Package Management | `uv` (Python), `npm` (frontend) |

---

## Project Structure

```
multi-agent-insurance-claims-investigation-assistant/
├── agents/
│   ├── fraud_node.py            # Random Forest inference node
│   ├── recommendation_node.py   # LLM reasoning node (Ollama)
│   └── feedback_node.py         # Persists investigator feedback
├── retrieval/
│   ├── build_index.py           # One-time: builds the Chroma index from historical claims
│   ├── retrieval_node.py        # RAG retrieval node
│   ├── chroma_client.py         # Single shared Chroma index instance
│   └── chroma_db/               # Persisted vector index (generated)
├── db/
│   └── claims_store.py          # SQLite store for every submitted claim
├── data/
│   ├── insurance_claims.csv     # Original historical training data
│   ├── claims.db                # All submitted claims (generated)
│   └── feedback.db              # Investigator feedback log (generated)
├── models/
│   └── model.pkl                # Trained fraud classifier
├── frontend/
│   ├── src/
│   │   ├── App.jsx              # Shell: sidebar + top bar + view routing
│   │   ├── Sidebar.jsx          # Navigation
│   │   ├── StatCards.jsx        # Dashboard summary stats
│   │   ├── ClaimsList.jsx       # Claims table (list view)
│   │   ├── ClaimForm.jsx        # New claim submission form
│   │   ├── ClaimDetail.jsx      # Fraud analysis + evidence + feedback form
│   │   ├── api.js               # Fetch wrapper over the FastAPI endpoints
│   │   └── App.css              # Styling
│   └── package.json
├── tests/
│   └── test_fraud_node.py
├── graph.py                     # LangGraph orchestration (StateGraph)
├── main.py                      # FastAPI application (backend entrypoint)
├── retrain_model.py             # Manually-triggered retraining script
└── pyproject.toml
```

---

## How It Works

### 1. Fraud Scoring (`agents/fraud_node.py`)
Encodes the claim's categorical fields using fixed mappings matched to the original training data, then runs it through the trained Random Forest. Returns a fraud probability, human-readable risk signals, and flags claims for investigation if the score exceeds a threshold **or** if any numeric field falls outside the training data's range (the model would be extrapolating).

### 2. Evidence Retrieval (`retrieval/retrieval_node.py`)
Embeds the claim as a natural-language summary and retrieves the 5 most similar historical claims from a ChromaDB index — including their **confirmed** fraud outcomes, giving the next step real evidence rather than just a bare probability.

### 3. Recommendation (`agents/recommendation_node.py`)
Feeds the fraud score and retrieved evidence to a local Llama 3.1 model with a strict system prompt, returning structured JSON: `recommendation` (approve / investigate / deny_pending_review), `confidence`, `key_factors`, and `next_steps`. Falls back safely to "investigate" if the model's output can't be parsed.

### 4. Human Review (`agents/feedback_node.py`)
An investigator confirms or overrides the AI's recommendation via the dashboard. This is captured as ground truth — not fed back automatically, since a Random Forest can't be incrementally trained on single examples.

### 5. Continuous Improvement
- **Retrieval** updates immediately: the confirmed claim is embedded and inserted into the live Chroma index, so the very next investigation can retrieve it as evidence.
- **The classifier** updates on demand: `retrain_model.py` combines the original training data with all accumulated feedback and refits the Random Forest using the exact preprocessing and hyperparameters from the original training notebook.

---

## Setup & Installation

### Prerequisites
- Python 3.12+ with [`uv`](https://docs.astral.sh/uv/)
- Node.js + npm
- [Ollama](https://ollama.com) installed locally, with `llama3.1` pulled:
  ```bash
  ollama pull llama3.1
  ```

### Backend
```bash
uv sync
```

### Frontend
```bash
cd frontend
npm install
```

### Build the retrieval index (one-time, before first run)
```bash
uv run python retrieval/build_index.py
```

---

## Running the App

Two servers need to run side by side:

**Terminal 1 — backend (from the project root):**
```bash
uv run uvicorn main:app --reload
```
Runs at `http://127.0.0.1:8000`. Interactive API docs at `http://127.0.0.1:8000/docs`.

**Terminal 2 — frontend (from `frontend/`):**
```bash
npm run dev
```
Runs at `http://localhost:5173`.

Open `http://localhost:5173` in your browser.

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/claims` | Submit a new claim — runs the full pipeline and returns the fraud result, retrieved evidence, and recommendation |
| `GET` | `/claims` | List all submitted claims (summary view, for the dashboard) |
| `GET` | `/claims/{claim_id}` | Full detail for one claim |
| `POST` | `/claims/{claim_id}/feedback` | Submit an investigator's decision and confirmed outcome |

---

## The Feedback Loop

This project deliberately separates two kinds of "learning," since they behave very differently:

- **Retrieval (online):** ChromaDB supports incremental inserts. A confirmed claim is embedded and added to the index the moment feedback is submitted — no rebuild, no downtime.
- **Classification (batch):** A Random Forest **cannot** be incrementally trained on one new example. New feedback accumulates in `data/feedback.db`, and `retrain_model.py` is run manually to refit the model on the full combined dataset when enough new labeled data has accumulated (or when drift is suspected).

```bash
uv run python retrain_model.py
```
This overwrites `models/model.pkl`. There is currently no A/B validation gate before swapping models — see Future Work.

---

## Known Limitations & Future Work

- **No model versioning or validation gate.** `retrain_model.py` overwrites the production model directly. The natural next step is tracking retrains with **MLflow** — logging metrics per version and only promoting a retrained model if it doesn't regress on a held-out set.
- **No automated drift detection.** Retraining is currently manually triggered. MLflow (or a dedicated monitoring job) could trigger retraining automatically when input distributions shift.
- **Fixed categorical encodings.** The model's `ENCODINGS` are frozen to the original training data's categories; a feedback claim with a never-before-seen category (e.g. a new `auto_make`) is skipped during retraining rather than expanding the encoding space.
- **No authentication.** The API and dashboard have no access control — fine for a local/demo deployment, not production-ready as-is.
- **Single local LLM.** The recommendation node depends on a locally running Ollama instance; there's no fallback to a hosted API if Ollama is unavailable.

---

## License

This project was built as a personal portfolio/interview-preparation project.(replica of what i did during my internship with openai api key)