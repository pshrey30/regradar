# Self-Serve Admin Signup & Organization Onboarding — Design

## Context

Today, RegRadar's only signup path is invite-gated (`POST /v1/auth/signup`,
`src/regradar/schemas/auth.py`): a `SELF_SELECTABLE_ROLES` frozenset
excludes `ApiKeyRole.ADMIN`, so the only way an org gets an Admin account
is another Admin issuing an Admin-role invite, and the only way an Admin
account exists at all today is the `regradar create-api-key --role admin`
CLI command. There is also no user-facing API for `OrganizationProfile`
(`src/regradar/models/organization_profile.py`) — it can only be written
by direct DB access, and `relevance_agent.py` silently scores every org's
filings the same way regardless of whether that profile is populated.

The user wants a real product onboarding path: a brand-new visitor with no
invite should be able to sign up, create an organization, and become that
org's Admin — and that org's filings should not be scored until the Admin
has entered real organization context (industry, watchlist entities,
products, risk priorities, business description), because that context is
what `relevance_agent.py` uses to produce accurate `relevance_score` and
`matched_signals` (verified live earlier in this project: a filing
mentioning a watchlisted entity and product scored 0.95 with both named in
the rationale, versus a generic score without that context).

**Explicitly confirmed with the user:**
- Self-serve org creation bypasses invites entirely — this supersedes the
  existing docstring/comment in `schemas/auth.py` stating "an Admin
  account is only ever created by another Admin issuing an Admin-role
  invite," which will be updated as part of this change.
- Email verification is **out of scope for this spec** — deferred to a
  future change. Signup and org creation are immediate, no confirmation
  step.
- The pipeline gate on missing `OrganizationProfile` is a **hard block**:
  filings for an org with no complete profile are not scored at all
  (no generic/default scoring), not merely flagged with a banner.
- All five `OrganizationProfile` fields (industry, business_description,
  watchlist_entities, products, risk_priorities) are required at
  onboarding, not optional-fill-later.
- `OrganizationProfile` remains editable only by Admin after onboarding,
  via the admin-only settings surface (`GET/PUT
  /v1/organizations/me/profile`) already recommended in prior discussion
  of this project — this spec is the first thing to actually build that
  endpoint.

## Approaches Considered

1. **Overload the existing `/v1/auth/signup` endpoint** with an
   `is_new_org`/`org_name` flag. Minimal new surface area, but conflates
   two different signup semantics (join-an-org-via-invite vs.
   create-a-new-org) in one request schema — rejected as a future
   maintenance hazard.
2. **New `POST /v1/auth/signup-org` endpoint, parallel to the existing
   one** (chosen). Its own schema, no `role`/`invite_code` fields since
   both are implied (Admin, no invite). Leaves the existing, already-
   tested invite path completely untouched.
3. **A separate `/v1/organizations` resource with its own creation
   endpoint, decoupled from auth.** More "correct" REST modeling
   long-term, but over-engineered for what is really one action (signup
   that happens to create an org), and reintroduces the
   create-a-resource-before-you're-authenticated bootstrapping problem
   from scratch. Rejected as YAGNI for this scope.

## Data Model

**New migration `migrations/versions/0027_org_signup_and_setup_gate.py`:**

1. **`FilingStatus` enum** (`src/regradar/models/enums.py:23-33`) — add
   `NEEDS_ORGANIZATION_SETUP` alongside the existing `INGESTED,
   CLASSIFYING, NEEDS_CLASSIFICATION, NEEDS_REVIEW, RETRIEVING,
   ANALYZING, SUMMARIZING, DELIVERING, COMPLETE, FAILED`.

No new columns on `ApiKey` or `Organization` — both already carry
everything needed (`ApiKey.organization_id`, `.role`, `.email`,
`.password_hash`; `Organization.id`, `.name`). Org creation is one
`Organization` row + one `ApiKey(role=ADMIN)` row in the same
transaction. `org_name` uniqueness is not enforced — organizations are
never looked up by name, so a duplicate name is cosmetic, not a
correctness issue.

**"Complete" defined precisely.** `OrganizationProfile` currently has
`industry`/`business_description` nullable, and
`watchlist_entities`/`products`/`risk_priorities` non-nullable-but-
default-empty-list. "Complete" means: `industry IS NOT NULL AND
business_description IS NOT NULL AND array_length(watchlist_entities,1) >
0 AND array_length(products,1) > 0 AND array_length(risk_priorities,1) >
0`. No schema change needed for this — it's a predicate, centralized as
one function `organization_profile.is_complete(profile) -> bool` so the
onboarding-redirect check and the pipeline gate can't drift apart from
each other.

**Schemas (`src/regradar/schemas/auth.py`):**
- New `SignupOrgRequest{org_name: str, display_name: str, email: str,
  password: str}` — no `role`/`invite_code` fields. Reuses the same email
  format and password strength validators as `SignupRequest` rather than
  duplicating them.
- New `OrganizationProfileRequest`/`OrganizationProfileResponse` schemas
  in a new `src/regradar/schemas/organization.py` mirroring the model's
  five fields.
- Update the `schemas/auth.py` module docstring/comment currently stating
  "no email-confirmation flow" / "Admin only created via invite" — the
  self-serve path makes the second half of that sentence false.

## API Surface

- `POST /v1/auth/signup-org` — creates `Organization`, then
  `ApiKey(role=ADMIN, email=..., password_hash=...)`, in one transaction.
  Returns 201 with no session, matching the existing `/v1/auth/signup`
  convention of "create only, then log in separately." Duplicate email →
  409 (same as existing signup). Invalid/empty `org_name` or weak
  password → 422 (same validators, same error shape as existing signup).
- `GET /v1/organizations/me/profile` — returns the calling key's org's
  `OrganizationProfile`, or a 200 with all fields empty/null if the row
  doesn't exist yet (not a 404 — "doesn't exist" and "exists but empty"
  are the same UI state: show the onboarding form). Any authenticated
  role may read.
- `PUT /v1/organizations/me/profile` — **Admin-only** (403 for any other
  role, same pattern as the six existing inline admin checks noted
  earlier in this project — this endpoint uses the shared `require_admin`
  dependency, extracted from those six call sites as part of this work
  since a seventh copy-paste is the point past which it should be a
  dependency). Upserts all five fields in one transaction. On success,
  synchronously requeues any filings stuck in
  `NEEDS_ORGANIZATION_SETUP` for that org: `UPDATE filings SET
  status='INGESTED' WHERE organization_id=:org AND
  status='NEEDS_ORGANIZATION_SETUP'`, then triggers the same
  `process_pending_filings` path ingestion already uses today. A `PUT`
  with any of the five fields empty/missing is rejected with 422 before
  any DB write — partial saves never happen, so there's no
  half-complete-profile state to reason about beyond "row doesn't exist
  yet."

## Pipeline Gate

At the top of `src/regradar/workers/pipeline_tasks.py`'s per-filing
processing, before `graph.ainvoke(...)`: if the filing's org has no
`OrganizationProfile` row, or `is_complete(profile)` is false, set
`filing.status = FilingStatus.NEEDS_ORGANIZATION_SETUP` and return —
the LangGraph pipeline never runs, so zero LLM calls are spent scoring a
filing with no organizational context. This is a pure status assignment,
same shape as the existing `NEEDS_CLASSIFICATION`/`NEEDS_REVIEW` early
returns already in this function.

## Frontend

- **Signup page**: two explicit paths — "I have an invite code" (existing
  form, unchanged) and "Create a new organization" (new form: org name,
  your name, email, password) — the latter posts to `/v1/auth/signup-org`
  and then redirects to `/login?created=true`, same as today's signup
  redirect.
- **Route guard** (`App.tsx`): once logged in, before rendering any other
  route, check `GET /v1/organizations/me/profile`. Incomplete → hard
  redirect to `/onboarding` (not a dismissible banner, consistent with
  the backend's hard-block posture — a user who could get around the
  redirect would just hit 422s from the pipeline anyway, so the guard is
  a UX convenience, not the actual enforcement boundary). Complete →
  normal dashboard routes.
- **`/onboarding`** (Admin-only route — a non-Admin landing here, e.g. an
  invited teammate added before the Admin finishes onboarding, sees a
  "waiting on your Admin to finish setup" message instead of the form):
  a form for all five fields (industry: free text; business_description:
  textarea; watchlist_entities/products/risk_priorities: chip-style
  multi-entry lists, matching the admin-settings UI pattern already
  recommended for `OrganizationSettings.tsx` in prior discussion — this
  spec's onboarding form and that later settings page share the same
  form component). Submits via `PUT /v1/organizations/me/profile`, then
  redirects to the dashboard.
- **`OrganizationSettings.tsx`** (admin-only, edit-after-onboarding): out
  of scope for *this* spec to build in full — it was already recommended
  separately — but this spec's `PUT /v1/organizations/me/profile`
  endpoint and the onboarding form component are exactly what that page
  will reuse, so no rework is expected when it's built.

## Error Handling

- **Duplicate email on `signup-org`**: 409, same shape as existing
  `/v1/auth/signup`.
- **Weak password / empty org_name**: 422 before any row is written —
  validation runs before the transaction opens.
- **Network drop mid-onboarding-submit**: the `PUT` is all-or-nothing at
  the DB level (single transaction, all five fields or none); the
  frontend stays on `/onboarding` until a subsequent `GET` confirms
  completeness, so a failed submit just means "try again," never a
  half-saved profile.
- **Non-Admin hits `/onboarding` or calls `PUT` directly**: 403 from
  `require_admin`; frontend shows the "waiting on your Admin" message
  rather than the form.
- **Filing arrives for an org mid-onboarding** (ingested before the
  Admin finishes): parked at `NEEDS_ORGANIZATION_SETUP`, picked up by the
  same requeue the profile-save endpoint triggers — no separate poller
  needed, no filing is silently dropped.

## Testing

- Unit: `signup-org` creates exactly one `Organization` + one
  `ApiKey(role=ADMIN)`; duplicate email → 409; weak password → 422.
- Unit: `organization_profile.is_complete()` — empty row, all-fields-set
  row, and each of the five fields individually missing/empty (six cases
  total) each return the correct bool.
- Unit: `GET /v1/organizations/me/profile` for a non-existent profile
  returns 200 with empty fields, not 404.
- Unit: `PUT /v1/organizations/me/profile` — non-Admin → 403; Admin with
  one field missing → 422, no row written; Admin with all fields →
  upserts and requeues any `NEEDS_ORGANIZATION_SETUP` filings for that
  org.
- Unit: pipeline gate — filing for an org with no profile row → status
  becomes `NEEDS_ORGANIZATION_SETUP`, `graph.ainvoke` never called
  (mock asserts zero calls); filing for an org with a complete profile →
  pipeline runs as today, unchanged.
- Integration: full flow — `signup-org` → login → `GET profile` (empty)
  → `PUT profile` (all fields) → previously-parked filing transitions
  out of `NEEDS_ORGANIZATION_SETUP` and completes processing.
