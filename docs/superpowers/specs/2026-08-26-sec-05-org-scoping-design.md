# SEC-05 — Organization-Scoping Scaffolding — Design

## Context

RegRadar has never had an organization/tenant concept anywhere in its schema — every prior ticket
that touched this (AGENT-10's delivery destinations, API-02's key resolution) explicitly deferred
it, citing this exact ticket. SEC-01 already built real, DB-enforced RLS scoped by *role*
(`app.current_role`) and, for webhooks, by *owning key* (`app.current_api_key_id`). SEC-05 adds the
third and final scoping dimension the Security & Access Document calls for: *organization*.

**Explicit scope constraint (user directive):** this is a resume/portfolio project, not a real
multi-tenant SaaS product. The goal is a real, demonstrable proof that organization-level isolation
works end-to-end at the database level — not a full organization-management system. No
organization-management API, no admin UI, no invitation/billing flows, no CLI surface for creating
organizations (the isolation proof creates its second organization directly via SQL). Keep the
footprint to exactly what the ticket's own acceptance criteria requires, demonstrated for real.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Which tables get `organization_id` | `filings`, `webhooks`, `deliveries`, `source_configs`, `api_keys` (the ticket's literal list) | These are the tables that own organization identity directly |
| `filing_chunks`/`extractions`/`briefs` | No new column — RLS policies join to `filings.organization_id` instead | They're 1:1/1:many children of a filing; a duplicated column risks drifting from the parent's real org |
| `eval_runs` | Stays org-less, untouched by this ticket | A system-wide quality metric (eval harness results), not customer data — organization scoping doesn't apply |
| Default organization | One row seeded in the migration; all 5 tables' `organization_id` backfilled to it, then set `NOT NULL` | Matches SEC-01's `regradar_app` role-provisioning precedent: nullable → backfill → `NOT NULL`, never a column that's nullable forever by accident |
| RLS composition | Every SEC-01 policy gains an **additional, ANDed** `organization_id = current_setting('app.current_organization_id', true)::uuid` condition | Org scoping is a hard boundary layered under existing role/ownership scoping, never an alternative path that could widen access |
| `service` (workers/CLI) | Continues to bypass org-scoping the same way it already bypasses role-scoping | It's the internal pipeline actor operating across the whole system, not a customer-facing caller — unchanged from SEC-01's own precedent |
| Admin role | Becomes **org-scoped**, not a cross-org super-admin | Matches real multi-tenant SaaS semantics and the explicit "don't build more than needed" directive — nothing in this ticket asks for a cross-org admin capability |
| Auth/session wiring | `AuthenticatedKey` gains `organization_id: UUID`; `set_rls_context()` gains a third GUC (`app.current_organization_id`) | Same mechanism SEC-01 already established for role/key-id — extended, not replaced |
| `GET /v1/me` | Returns the caller's real `organization_id` instead of the hardcoded `null` from API-11 | Leaving it null after this ticket adds the real column would be stale and misleading for no reason |
| Isolation proof | A real integration test: create a second org + a second `api_keys` row directly via SQL, insert data scoped to org 2, confirm org 1 (including Admin) genuinely cannot see it — verified at the database level, the same rigor SEC-01's own suite used | Matches the ticket's explicit acceptance criteria ("write a test creating a second organization and confirming its data is fully isolated... under every existing RLS test from SEC-01") without building any org-management surface just to support the test |
| Org creation surface | None — no CLI command, no API endpoint | Explicitly out of scope per the resume-project directive; the isolation test is the only place a second org is ever created, via direct SQL |

## Data Flow

1. Migration creates `organizations` (`id`, `name`, `created_at`), adds nullable `organization_id`
   FK to the 5 tables, seeds one default org, backfills every existing row, sets `NOT NULL`.
2. Migration updates every SEC-01 RLS policy on those 5 tables (plus `filing_chunks`/`extractions`/
   `briefs`, via the `filings` join) to AND in the organization check.
3. `deps.py`'s `get_current_key` resolves `organization_id` from the matched `api_keys` row onto
   `AuthenticatedKey`.
4. `api/middleware/rate_limit.py`'s `get_authenticated_db` passes `organization_id` through to
   `set_rls_context`, alongside the existing role/key-id GUCs.
5. Every existing authenticated route is unaffected in its own code — org-scoping happens
   transparently underneath, the same way SEC-01's role-scoping already does.
6. `GET /v1/me` reads `key.organization_id` directly instead of hardcoding `null`.

## Testing

- Unit tests: `deps.py`'s `get_current_key` resolves `organization_id` correctly (mocked DB row);
  `/v1/me` returns the real value.
- Real Postgres integration test (extends the `test_row_level_security.py` pattern): seed a second
  `organizations` row and a second `api_keys` row assigned to it, insert a filing under org 2,
  confirm an org-1 key (including Admin) sees zero rows for it, and an org-2 key sees it correctly —
  proving isolation is enforced by Postgres itself, not application code.
- Existing SEC-01 role/ownership tests must keep passing unchanged (both keys used in those tests
  belong to the same default org, so nothing about their existing behavior should change).

## Non-Goals (explicit)

- No organization-management API or UI.
- No CLI command to create organizations.
- No cross-org super-admin capability.
- No changes to `eval_runs`' scoping.
- No changes to how `service`/workers resolve their identity.
