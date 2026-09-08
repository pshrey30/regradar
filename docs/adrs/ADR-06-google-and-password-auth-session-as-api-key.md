# ADR-06: Google SSO + Email/Password, Session Cookie as a Rotating API Key

**Decision:** FE-02 ships two real, independent sign-in paths — Google OIDC and email/password —
not the ticket's literal Google-and-Microsoft wording. Both resolve to an `api_keys` row exactly
like a directly-issued key does: the session cookie's raw value is a regular API key (generated
the same way `create-api-key` does), its hash is what `api_keys.key_hash` stores, and
`get_current_key` authenticates a cookie through the identical lookup/RLS/rate-limit path as an
`Authorization: Bearer` header — no separate cookie-specific auth code path exists for either
method. A first-time signup (either path) provisions into the single, first-created organization
(no organization-management surface exists yet — SEC-05's precedent) with `ApiKeyRole.ANALYST`,
not Admin: self-service sign-in has no invitation step, so a new identity defaults to the
lowest-privilege real role rather than escalating itself.

Google and email/password are independent signup paths into the same `api_keys` table, not linked
accounts — signing up with the same email both ways creates two separate rows today. Account
linking is real, legitimate future scope, deliberately not built here: it needs a decision this
ticket has no basis for (which identity "wins" a role/org conflict) that's better made when there's
an actual second real use case asking for it.

**Rationale:** Real OIDC needs a real registered OAuth app (client ID/secret) from an identity
provider's console — this project had no such app provisioned by default, the same real-credential
gap ADR-05 already covers for OpenAI/Hugging Face. Google was chosen as the one real SSO provider
to build and live-verify end-to-end (registration is free, takes minutes, needs no business
verification for a "Testing" publish-status app with the developer's own account as a test user)
rather than building both Google and Microsoft against zero real credentials. The project owner
registered a real Google Cloud OAuth app specifically for this ticket and provided the client
ID/secret directly (added to `.env`, never committed) — this is a live-verified real login, not a
stand-in. Email/password was added second, at the project owner's explicit request, so sign-in
doesn't depend on any external identity provider at all — a real, complete path with no OAuth app
required, using bcrypt (slow, salted) rather than `core/api_keys.py`'s fast unsalted SHA-256, which
is correct for a high-entropy random API key but would be a real vulnerability for a low-entropy
user-chosen password.

Reusing the existing `api_keys`/`get_current_key` machinery for the session cookie, instead of a
parallel session-token concept, means SEC-01's RLS policies, API-03's rate limiting, and every
existing authenticated route work for a cookie-based caller with zero additional code — API-11's
own docstring anticipated this exact design before FE-02 was built ("this route works identically
for a direct API key today and, once FE-02's SSO session resolves to an API key behind the
scenes, for a session cookie too").

**Tradeoff:** Microsoft/Azure AD SSO is not built. Adding it later is mechanical (the same
`core/sso.py` shape, a second provider's authorize/token endpoints and JWKS-verified ID token), not
a redesign, since `ApiKey.sso_provider` is already a free-text column rather than a Google-specific
enum. Logging in again (either method) rotates the row's `key_hash`, which means only one active
session per identity at a time — a deliberate single-session simplification appropriate for a
portfolio project, not something a real multi-device product would ship. Email/password has no
email-confirmation step (an unconfirmed email can sign up and log in immediately) and no
password-reset flow — both are real, out-of-scope gaps for a resume project with no email-sending
infra wired to auth. Brute-force protection on `/v1/auth/login` is a simple Redis counter
(5 failed attempts per email locks it out for 15 minutes), not a production-grade rate limiter —
sufficient to demonstrate the practice, not hardened against a distributed attack.

**Implementation:** `src/regradar/core/sso.py` (the actual Google protocol calls: authorize URL,
token exchange, ID-token verification via `google-auth`'s real signature check against Google's
public keys) and `src/regradar/core/passwords.py` (bcrypt hash/verify for email/password) sit
behind `src/regradar/api/routers/auth.py`'s five routes: `/v1/auth/google/login`,
`/v1/auth/google/callback`, `/v1/auth/signup`, `/v1/auth/login`, and one shared `/v1/auth/logout`
— cookie issuance/rotation/deletion, CSRF `state` cookie for the Google flow, find-or-create
against `api_keys.sso_provider`/`sso_subject_id` or `.email`/`.password_hash` depending on path.
Migrations `0014` (SSO identity columns) and `0015` (email/password columns) each add a partial
unique index. `api/deps.py`'s `get_current_key` gained a `regradar_session` cookie fallback
alongside the existing `Authorization` header. Frontend: `src/auth/AuthContext.tsx` calls
`GET /v1/me` once on load; `src/auth/useAuth.ts`'s `canSeeNavItem` gates the nav shell per the
Security & Access Document's role matrix; `src/lib/api.ts` centralizes the 401-redirects-to-login
behavior so no individual route has to implement it; `src/pages/Login.tsx` offers both sign-in
paths on one screen, with a signup/login toggle for the email/password form.
