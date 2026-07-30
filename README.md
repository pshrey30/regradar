# RegRadar

A multi-agent regulatory intelligence system that monitors SEC, FDA, and FINRA filings in real
time, triages risk, extracts structured obligations, and delivers plain-English briefs to Slack
and email within minutes of publication.

Full product and architecture context lives in `Document files/` (PRD, Technical Architecture
Document, Feature Ticket List, Security & Access Document, Frontend Specification Document).

## Status

This repository is being built out ticket-by-ticket per the Feature Ticket List. Current state:
repository scaffolding only — no business logic yet.

## Local setup

1. Ensure Python 3.11+ is installed.
2. Clone the repository and `cd` into it.
3. Create a virtual environment: `python3 -m venv .venv && source .venv/bin/activate`.
4. Install the project in editable mode with dev dependencies: `pip install -e ".[dev]"`.
5. Copy `.env.example` to `.env` and fill in the required values (see comments in the file).
6. Start the local stack with `docker compose -f infra/docker-compose.yml up` (once FOUND-03 lands).
7. Run database migrations: `alembic upgrade head` (once FOUND-02 lands).
8. Run the test suite: `pytest tests/unit -v`.
9. Start the API locally: `uvicorn regradar.api.main:app --reload` (once API-01 lands).
10. Visit `http://localhost:8000/docs` for the OpenAPI schema.

## Repository layout

- `src/regradar/agents/` — LangGraph agent implementations and shared pipeline state
- `src/regradar/ingestion/` — Prefect flows and per-regulator source connectors
- `src/regradar/rag/` — chunking, embeddings, hybrid retrieval, reranking
- `src/regradar/api/` — FastAPI application, routers, middleware
- `src/regradar/workers/` — Celery tasks that run the pipeline asynchronously
- `src/regradar/delivery/` — Slack, SendGrid, and webhook delivery clients
- `src/regradar/eval/` — Ragas, ROUGE-L, and precision/recall evaluation harness
- `migrations/` — Alembic database migrations
- `tests/` — unit and integration tests, plus fixture filings
- `docs/adrs/` — architecture decision records
- `infra/` — Docker and deployment configuration

## Contributing

See `docs/adrs/` for the rationale behind major technical decisions before proposing changes to
orchestration, storage, or model routing.
