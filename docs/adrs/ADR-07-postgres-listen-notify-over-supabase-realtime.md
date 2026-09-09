# ADR-07: Postgres LISTEN/NOTIFY + WebSocket Over Supabase Realtime

**Decision:** FE-04's live-updating filing status is built on a Postgres trigger that
`pg_notify`s on `filings.status` changes (migration 0016), consumed by a WebSocket endpoint
(`GET /v1/filings/{id}/status/ws`) on our own FastAPI backend that `LISTEN`s on that channel and
forwards matching notifications to the browser — not Supabase Realtime, as FE-04's ticket
literally specifies.

**Rationale:** Supabase Realtime requires a real Supabase-hosted Postgres project; this
deployment's Postgres is local Docker (`.env`'s own comment: "local Docker Compose
Postgres+pgvector, not Supabase cloud"), and `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` are
placeholder-only. Standing up a real cloud Supabase project — and, separately, a publishable/anon
key with RLS-safe realtime read access, since the service-role key can never be shipped to a
browser — was out of scope for a portfolio-project dev environment. Postgres LISTEN/NOTIFY
achieves the identical user-facing behavior (the status pill updates live, no manual refresh) on
infrastructure this deployment actually has.

**Tradeoff:** LISTEN/NOTIFY is a single-database-instance mechanism — it doesn't fan out across
multiple Postgres replicas the way Supabase Realtime (built on logical replication) would, and
each open WebSocket holds a dedicated (non-pooled) asyncpg connection for the life of the
subscription, a connection-count cost Realtime's own infrastructure would absorb instead. Neither
matters at this project's scale, but a real multi-instance production deployment would need to
either centralize LISTEN through one process (fanning out to WebSocket clients via Redis pub/sub)
or migrate to genuine Supabase Realtime once a real Supabase project exists.

**Implementation:** `core/pg_listen.py`'s `listen()` context manager opens one asyncpg connection
per subscription; `api/routers/filings.py`'s `stream_filing_status` websocket route authenticates
via the existing `get_current_key` dependency (the same session-cookie-as-API-key path FE-02
built), sends the filing's current status immediately on connect, then one message per matching
NOTIFY until the client disconnects.
