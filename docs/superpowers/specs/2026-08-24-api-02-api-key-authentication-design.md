# API-02 — API Key Authentication — Design

## Context

`api_keys` exists (FOUND-02) but has no `role` column, even though the Security & Access
Document's entire permission model (Admin / Analyst / Executive-CCO / Legal Counsel / Eng Lead)
hangs off a key's role. API-02's own AI Coding Prompt already assumes a resolved role exists
(`AuthenticatedKey` "carrying the key's role and organization"), so this ticket has to add the
column, not just the app code around it.

Two further gaps, confirmed with the user before design:

- **No ticket anywhere issues API keys.** FE-07 (Nice-to-have, V2) is the only place a
  key-creation endpoint is even mentioned, and it explicitly defers building one. Without some
  way to mint a real key, API-02 has nothing to authenticate against.
- **API-02's ticket-list dependency line lists SEC-01** (row-level security), which isn't built.
  Per this project's precedent of pragmatic reordering (AGENT-03's Ollama swap, AGENT-10 skipping
  organization scoping), API-02 proceeds now with app-level enforcement only; SEC-01 becomes its
  own later, database-level ticket layered on top.

Organization is intentionally **not** part of this ticket. AGENT-10 already established that no
organization/tenant concept exists anywhere in the schema (`SEC-05` — Should-have — would add
one) and that RegRadar's actual deployment is single-tenant. Inventing a fake single-org concept
here just to satisfy the ticket text's "and organization" wording would be scope creep the
project doesn't need yet.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Role storage | New Postgres enum column (`api_keys.role`), migration `0007` | Matches every other categorical field in this schema (`FilingStatus`, `RiskLevel`, ...) — DB-level guarantee, not just app-level validation |
| Key issuance | Small internal CLI command (`regradar.cli create-api-key`) | No ticket builds a real issuance endpoint yet; a CLI is enough to bootstrap real keys for tests and live verification without scope-creeping into FE-07's V2 work |
| Hashing | SHA-256, single indexed `WHERE key_hash = ?` lookup | Bcrypt/argon2 are salted — can't be looked up by equality, only iterated and checked per-row. API keys are already high-entropy random strings (unlike user passwords), so a fast deterministic hash is the right tool, the same choice Stripe/GitHub make. Deviates from the ticket text's literal "bcrypt or argon2" wording; `config.api_key_hash_algorithm` default changes from `bcrypt` to `sha256` |
| Organization | Not resolved by this ticket | No org concept exists in the schema yet (AGENT-10 precedent); SEC-05 owns adding it |
| SEC-01 ordering | API-02 ships now with app-level checks only | RLS is a separate, database-level unit of work deserving its own design/test pass, not a bolt-on here |

## Components

### `core/api_keys.py` (new)

Shared by the CLI and the auth dependency so generation and verification can never drift:

- `generate_api_key() -> str` — `"rr_" + secrets.token_urlsafe(32)`, a high-entropy random
  string, prefixed so keys are recognizable in logs/configs (Stripe/GitHub-style prefixing)
- `hash_api_key(raw: str) -> str` — `hashlib.sha256(raw.encode()).hexdigest()`

### `models/enums.py`

New `ApiKeyRole(str, enum.Enum)`: `ADMIN`, `ANALYST`, `EXECUTIVE`, `LEGAL_COUNSEL`, `ENG_LEAD` —
values matching the Security Doc's role names, following the file's existing `pg_enum_values`
pattern.

### `models/api_key.py`

Adds `role: Mapped[ApiKeyRole]` (`SAEnum(ApiKeyRole, values_callable=pg_enum_values)`,
`nullable=False`).

### `migrations/versions/0007_add_api_key_role.py`

Adds the Postgres enum type and the `role` column, `NOT NULL` (the table is empty in every
environment so far — no backfill required). Downgrade drops the column and the enum type,
following the existing migration files' pattern.

### `api/errors.py` (new)

- `ApiError(HTTPException)` — carries a `code: str` alongside the HTTP status and message
- One exception handler, registered in `create_app()` (API-01), rendering
  `{"error": {"code": ..., "message": ..., "request_id": ...}}` — `request_id` sourced from
  API-01's existing `request_id_ctx` contextvar. Minimal for now (just what API-02's 401s need),
  but the shape every later ticket's errors (429, 404, 422) will reuse.

### `api/deps.py` (new)

```
AuthenticatedKey:
    id: UUID
    role: ApiKeyRole
    owner_label: str
    rate_limit_per_minute: int

async def get_current_key(authorization: str = Header(default="")) -> AuthenticatedKey
```

1. Parse `Authorization: Bearer <key>` — missing, empty, or wrong scheme → `401 invalid_api_key`
2. `hash_api_key(presented)`, single indexed `SELECT ... WHERE key_hash = :hash`
3. No match, or match with `is_active = False` → `401 invalid_api_key` (same generic message
   either way — never reveals which case, so probing can't distinguish "doesn't exist" from
   "revoked")
4. Match, active → update `last_used_at` (best-effort; a failed write here doesn't fail an
   otherwise-valid request), return `AuthenticatedKey`

A DB error during the lookup itself is **not** swallowed into a 401 — it propagates as a real
500, consistent with API-01's health check being the place that reports DB unavailability, not
auth silently mislabeling an outage as "invalid key."

### `api/routers/whoami.py` (new, throwaway)

`GET /v1/_whoami`, gated by `Depends(get_current_key)`, echoing back `{role, owner_label}`. No
real route exists yet to exercise the dependency through an actual HTTP request (API-04 etc.
aren't built) — this endpoint exists purely so the ticket's own acceptance criteria (missing
header / malformed header / valid key / revoked key / unknown key, all via HTTP) can be tested
end-to-end. Delete once a real authenticated route (API-04) exists to serve that purpose instead.

### `cli.py`

New `create-api-key` subcommand: accepts `--owner-label` and `--role`, generates + hashes a key,
inserts the `api_keys` row, prints the plaintext value once. This is the only way real keys get
created for local dev, tests, and live verification in this ticket's scope.

## Data flow

```
Request → RequestIdMiddleware (API-01, stamps X-Request-ID)
        → get_current_key dependency
              ├─ no/malformed header ──────────────→ 401 invalid_api_key
              ├─ hash lookup: no match / inactive ─→ 401 invalid_api_key
              └─ match, active ─────────────────────→ update last_used_at,
                                                        yield AuthenticatedKey
        → route handler (here: /v1/_whoami) receives AuthenticatedKey via Depends()
```

## Testing

- `tests/unit/api/test_deps.py` — via `TestClient` against `/v1/_whoami`: missing header,
  malformed header (`Authorization: not-bearer-format`), valid active key, revoked key
  (`is_active=False`), unknown key, and `last_used_at` actually updating on success
- `tests/unit/core/test_api_keys.py` — `generate_api_key`/`hash_api_key`: determinism of
  hashing, uniqueness of generation
- Live verification: use the new CLI to mint a real key against a real Postgres container, hit
  the real running app, confirm both the success and 401 paths for real — same bar every prior
  ticket has met

## Out of scope (explicitly deferred)

- Row-level security (SEC-01) — separate future ticket
- Organization/tenant scoping (SEC-05) — separate future ticket
- Rate limiting (API-03) — separate ticket, depends on this one
- A real admin-facing key-creation/revocation endpoint (FE-07, V2) — the CLI is a bootstrap
  mechanism for this ticket's own needs, not a replacement for that future endpoint
