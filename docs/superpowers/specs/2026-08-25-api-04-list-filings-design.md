# API-04 — GET /v1/filings — Design

## Context

This is the first real, non-throwaway authenticated business route in the API epic — everything
before it (`/health`, `/v1/_whoami`) existed to bootstrap and test the auth/rate-limit
infrastructure. It's gated by API-03's `enforce_rate_limit` (which itself wraps API-02's
`get_current_key`), so it's also the first real proof that the combined-dependency design actually
works for a production route, not just the throwaway one.

Two gaps between the ticket's literal text and the fuller Security & Access Document, confirmed
with the user before planning:

- The ticket's own AI Coding Prompt only describes a **field-level** Executive restriction (no
  extraction detail). The Security Doc's full permission matrix additionally restricts Executive
  to a **row-level** filter — only High/Critical risk filings — which the ticket text omits. Both
  are implemented; the Security Doc is the actual source of truth this project has followed since
  API-02.
- The list endpoint's response schema (`id, entity_name, filing_type, domain, risk_level,
  published_at, executive_brief`) never includes extraction fields for **any** role — extraction
  detail only appears in API-05's single-filing endpoint. So the ticket's field-level Executive
  restriction is automatically satisfied by construction here; it's still worth an explicit test
  (the ticket calls it out by name), but the row-level risk restriction is the restriction with
  actual teeth for this endpoint.

A third gap found during design: invalid query parameters (e.g. `?domain=not-real`) currently
produce FastAPI's default `{"detail": [...]}` 422 shape, not the shared `{"error": {...}}` envelope
every other error in this API uses (API-02's 401, API-03's 429). Confirmed with the user: fixed now
via a second exception handler, since this is the first route with real query-param validation and
every future route benefits immediately — same reasoning as API-01's ruff config fix.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Executive restriction | Both field-level (automatic) and row-level (risk intersected with `{HIGH, CRITICAL}`) | Security Doc is the real spec; ticket text is an incomplete paraphrase of it |
| Executive risk override | Silently intersect, never error | Matches the existing "silently narrow" pattern the ticket family already uses (API-07's persona override for Executive) |
| Filing scope | Only `status = 'complete'` (has a real `Brief`) | `executive_brief` is a required response field; a still-processing filing has nothing to put there |
| 422 error shape | Add a `RequestValidationError` handler to the shared envelope now | First route with real query-param validation; fixing it now benefits every future route, not just this one |
| Auth/rate-limit | `Depends(enforce_rate_limit)` | The whole point of API-03's combined dependency — every future route uses it exactly where it would have used `get_current_key` |
| `/v1/_whoami` | Keep mounted alongside the new route | User's call — not retiring it in this ticket |

## Components

### `api/errors.py` (modified)

Add a second exception handler, alongside the existing `ApiError` one:

```python
@app.exception_handler(RequestValidationError)
async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Invalid request parameters.",
                "request_id": request_id_ctx.get(),
            }
        },
    )
```

### `schemas/filings.py` (new)

```python
class FilingListItem(BaseModel):
    id: uuid.UUID
    entity_name: str
    filing_type: str
    domain: FilingDomain | None
    risk_level: RiskLevel | None
    published_at: datetime
    executive_brief: str

class FilingListResponse(BaseModel):
    data: list[FilingListItem]
    page: int
    page_size: int
    total: int
```

`domain`/`risk_level` are typed nullable defensively even though every `status=complete` filing
should have both set by the time triage+summarization have run — better to serialize `null` in a
theoretical data-corruption case than 500 on response validation.

### `api/routers/filings.py` (new)

`GET /v1/filings`, `Depends(enforce_rate_limit)`. Query params via FastAPI's typed `Query()`:
`domain: FilingDomain | None`, `risk: RiskLevel | None`, `since: datetime | None`,
`page: int = Query(1, ge=1)`, `page_size: int = Query(20, ge=1, le=100)`.

Query construction:
1. Base: `SELECT ... FROM filings JOIN briefs ON briefs.filing_id = filings.id WHERE filings.status
   = 'complete'`
2. `+ AND filings.domain = :domain` if `domain` provided
3. `+ AND filings.risk_level = :risk` if `risk` provided — **except** for an Executive-role caller,
   whose effective risk filter is always intersected with `{HIGH, CRITICAL}`: if they passed no
   `risk`, the filter becomes `risk_level IN (HIGH, CRITICAL)`; if they passed one that isn't in
   that set, the filter becomes unsatisfiable (empty result, not an error) — an inline `AND
   filings.risk_level IN (...)` built from the intersection, not a separate query path
4. `+ AND filings.published_at >= :since` if `since` provided
5. `ORDER BY filings.published_at DESC`
6. `total = ` same WHERE clause, `SELECT COUNT(*)`, no pagination
7. Page query: same WHERE + ORDER BY, `LIMIT :page_size OFFSET (:page - 1) * :page_size`

Response: `FilingListResponse(data=[...], page=page, page_size=page_size, total=total)`.

## Data flow

```
Request → RequestIdMiddleware → enforce_rate_limit → get_current_key
        → parse/validate query params (domain, risk, since, page, page_size)
              ├─ invalid enum/type value ──→ 422 validation_error (new handler)
        → build WHERE: status=complete [+ domain=?] [+ risk=?] [+ published_at >= since]
              └─ role == EXECUTIVE ─→ intersect risk filter with {HIGH, CRITICAL}
        → COUNT(*) with same WHERE  →  total
        → SELECT ... ORDER BY published_at DESC LIMIT page_size OFFSET (page-1)*page_size
        → JOIN briefs for executive_brief
        → {"data": [...], "page": ..., "page_size": ..., "total": ...}
```

## Error handling

- Invalid `domain`/`risk` enum value, non-integer `page`/`page_size`, or `page_size` over 100 → 422
  through the new shared envelope
- No matching filings (any role, any filter combo, including an Executive whose requested risk
  doesn't intersect `{HIGH, CRITICAL}`) → `200` with `"data": []`, `"total": 0` — empty is not an
  error
- Auth/rate-limit failures unchanged from API-02/API-03 (401/429 through the existing envelope)

## Testing

`tests/unit/api/test_filings_route.py`, DB session mocked the same way `test_deps.py`/
`test_whoami_route.py` already do:

- Domain filter narrows results; risk filter narrows results; `since` filter narrows results;
  pagination fields (`page`, `page_size`, `total`) are correct
- Only `status=complete` filings appear — a filing with no `Brief` never appears even if it
  matches other filters
- Executive-role key requesting no risk filter still only gets High/Critical; Executive requesting
  `risk=low` gets an empty result, not an error or a 403
- Non-Executive roles are unaffected by the Executive-only intersection
- Response never contains any extraction-shaped key (explicit ticket requirement)
- Invalid `?domain=` value returns 422 through the shared envelope
- `tests/unit/api/test_errors.py` gets a new test for the `RequestValidationError` handler itself,
  independent of the filings route

Live verification: real Postgres, a few real `Filing`+`Brief` rows across domains/risk levels
(including at least one non-complete filing to prove it's excluded), a real Admin key and a real
Executive key, confirm filtering/pagination/role-restriction all work against real data, then clean
up — same bar as every prior ticket.

## Out of scope (explicitly deferred)

- Organization/tenant scoping — no org concept exists in the schema yet (SEC-05, deferred, same
  precedent as API-02/API-03)
- Row-level security (SEC-01) — app-level checks only, same precedent as API-02/API-03
- `LAUNCH-01`'s P99 <500ms load-test validation — this ticket implements the endpoint; formal load
  testing is a separate, later ticket
