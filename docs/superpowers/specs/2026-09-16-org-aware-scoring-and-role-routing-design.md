# ORG-11 — Organization-Aware Relevance Scoring & Role-Routed Alerts

## Context

Today, `triage_node` and `analyze_node` classify a filing's `domain`/`risk_level` and extract
`affected_products`/`key_entities`/`competitor_mentions`/`risk_flags`/`obligations` identically
for every organization — nothing in the pipeline knows what business the receiving organization
is actually in. `Organization` (SEC-05) is deliberately minimal (`id`, `name` only) — "no
organization-management surface... just the scaffolding RLS needs." `Filing.priority_score`
exists as a column but nothing has ever set it. Alert delivery (`deliver_node`) is org-wide only:
one Slack webhook and one email recipient per org (`organization_delivery_settings`), plus
per-API-key `Webhook` rows filterable by `filter_domain`/`filter_min_risk`. There is no notion of
routing an alert to a specific *role* — despite `domain_scope.py` already mapping roles to
domains for dashboard visibility (`ROLE_DOMAIN_RESTRICTIONS`: `ANALYST`→financial,
`LEGAL_COUNSEL`→clinical+environmental, `ENG_LEAD`→engineering).

This ticket closes both gaps: (1) score and explain a filing's relevance to the *specific*
organization's business, not just its generic severity, and (2) route the alert to the role(s)
whose domain it falls in, with content explaining what happened, why it matters to that org, and
what to do — reusing the existing role/domain mapping rather than inventing a second one.

## Data model

### `organization_profiles` (new table, 1:1 with `organizations`)

Same pattern as `organization_delivery_settings`/`source_configs` — a separate table rather than
columns on `Organization`, keeping that model's documented minimalism intact while giving this
feature its own home.

```
organization_id   UUID PK, FK -> organizations.id
industry          TEXT NULL           -- e.g. "medical device manufacturing"
business_description TEXT NULL        -- 1-3 sentences of free context
watchlist_entities TEXT[] NOT NULL DEFAULT '{}'  -- competitors/partners/vendors by name
products          TEXT[] NOT NULL DEFAULT '{}'   -- the org's own products/services
risk_priorities   TEXT[] NOT NULL DEFAULT '{}'   -- e.g. ["clinical trial safety", "data privacy"], priority order
updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
```

All fields nullable/empty-default — an org with no profile configured is a valid, supported
state (see "Graceful degradation" below), not an error.

### `organization_role_delivery_settings` (new table)

Per-role alert destinations, alongside the existing org-wide `organization_delivery_settings`
(which is unchanged and keeps running unconditionally for every filing, regardless of whether a
role-specific entry exists — role routing is additive, not a replacement for the org-wide
channel).

```
organization_id   UUID, FK -> organizations.id
role              api_key_role ENUM (existing type)
slack_webhook_url TEXT NULL
email             TEXT NULL
updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
PRIMARY KEY (organization_id, role)
```

Only rows for domain-restricted roles (`ANALYST`, `LEGAL_COUNSEL`, `ENG_LEAD`) are meaningful for
routing (see below), but the table itself doesn't restrict which roles can have a row — an org is
free to also set one for `EXECUTIVE` if it wants a separate channel, it just won't be
domain-triggered.

### `filings` — three new columns

Flat columns, matching how `domain`/`risk_level`/`priority_score`/`classification_confidence`
already live directly on `Filing` rather than a side table (this data is small, 1:1, and only
ever read alongside the rest of the row — unlike `Extraction`/`Brief`, which are large enough to
warrant their own tables).

```
relevance_rationale TEXT NULL     -- "why this matters to your business"
recommended_action  TEXT NULL     -- "what to do"
matched_signals      JSONB NULL   -- {"watchlist_entities": [...], "products": [...], "industry_alignment": "..."}
```

`priority_score` (existing column) finally gets written.

## Pipeline: new `relevance_node`

Inserted between `analyze` and `summarize`:

```
triage -> (retrieve) -> analyze -> relevance -> summarize -> deliver
```

### Loading the org profile without a DB-touching node

Every other node except `retrieve`/`deliver` is pure/sync with no DB access — `relevance_node`
should be too. `pipeline_tasks.py` already resolves the filing's `organization_id` before
building the graph; it loads the `OrganizationProfile` row there (one extra `db.get`, no new I/O
pattern) and passes it into the initial `PipelineState` as a new field:

```python
class OrgProfileSnapshot(BaseModel):
    industry: str | None = None
    business_description: str | None = None
    watchlist_entities: list[str] = []
    products: list[str] = []
    risk_priorities: list[str] = []

# PipelineState gains:
org_profile: OrgProfileSnapshot | None = None
relevance: RelevanceResult | None = None
```

A missing `OrganizationProfile` row (org never configured one) becomes `org_profile=None`, not an
error — `relevance_node` treats `None` the same as an all-empty snapshot.

### `relevance_agent.py` (new module)

Mirrors `analysis_agent.py`'s structure: tiered-router client selection, one LLM call with
`response_format=json_schema` (strict), retry once on malformed/invalid output, never raises out
of `relevance_node`.

Input to the prompt: `org_profile` fields + `state.extraction` (`affected_products`,
`key_entities`, `competitor_mentions`, `risk_flags`, `obligations`) + `state.domain` +
`state.risk_level`.

```python
class RelevanceResult(BaseModel):
    relevance_score: float          # 0.0-1.0
    matched_signals: dict           # {"watchlist_entities": [...], "products": [...], "industry_alignment": str}
    rationale: str                  # why this matters to *this* org
    recommended_action: str         # what to do
    model_used: str | None = None


def relevance_node(state: PipelineState) -> PipelineState:
    """Analogous to analyze_node: builds a prompt from org_profile + extraction,
    calls the model, validates+retries once. On failure (LLM error, malformed
    JSON, or state.extraction is None), returns a neutral default —
    RelevanceResult(relevance_score=0.5, matched_signals={}, rationale=<generic
    fallback string>, recommended_action=<generic fallback string>) — never
    leaves state.relevance as None, since deliver_node and priority_score both
    depend on it unconditionally. This never blocks or fails the pipeline."""
```

An org with an empty profile (all fields blank) still gets a `relevance_node` call, but the
prompt has nothing to match against — the model is expected to return a mid-range
`relevance_score` and say so in `rationale`; this is a **model behavior**, not special-cased code,
keeping the node's failure path (LLM/parse errors only) simple and matching the "spot-check never
raises" convention already established in `triage_agent.py`.

## Scoring

Computed in `relevance_node` (or immediately after it, still pure) and stored on `PipelineState`,
persisted onto `Filing` in `pipeline_tasks.py` alongside the existing domain/risk_level
persistence:

```python
_SEVERITY_MAX = 3  # RiskLevel.CRITICAL's SEVERITY_ORDER value, imported from triage_agent

def compute_priority_score(risk_level: RiskLevel, relevance_score: float) -> float:
    severity_component = SEVERITY_ORDER[risk_level] / _SEVERITY_MAX
    return round(100 * (0.5 * severity_component + 0.5 * relevance_score), 1)
```

Equal weighting: an objectively CRITICAL filing with zero org relevance and a LOW-risk filing
with perfect org relevance land at the same midpoint score — both are surfaced, neither
dominates. Per the "downrank, never suppress" decision, this score never gates delivery — every
filing an org is subscribed to via `source_configs` is still delivered/visible; it only affects
ranking/prominence in the dashboard and alert ordering.

## Role-routed alerts

### `domain_scope.py` addition

```python
def roles_for_domain(domain: FilingDomain | None) -> list[ApiKeyRole]:
    """Inverse of ROLE_DOMAIN_RESTRICTIONS: which domain-restricted roles this
    filing's domain should route to. Returns [] for domain=None (unclassified)
    or FilingDomain.OTHER (no restricted role maps to it) — such filings still
    reach Admin/Executive via the existing org-wide channels, just no
    role-specific fan-out. ADMIN/EXECUTIVE are never returned here (they're
    domain-unrestricted by design and already covered by org-wide delivery)."""
```

Deterministic, no LLM involved — routing must be reliable and auditable independent of model
output.

### `deliver_node` addition

The existing Slack/email/webhook fan-out (org-wide Slack, global email, per-API-key webhooks) is
**unchanged**. A new fan-out step is added after it:

```python
for role in roles_for_domain(state.domain):
    role_settings = await db.get(OrganizationRoleDeliverySettings, (filing.organization_id, role))
    if role_settings is None:
        continue
    message = build_role_alert_message(
        entity_name=filing.entity_name,
        filing_type=filing.filing_type,
        filing_url=filing.filing_url,
        risk_level=state.risk_level,
        priority_score=priority_score,
        rationale=state.relevance.rationale,
        recommended_action=state.relevance.recommended_action,
    )
    # send to role_settings.slack_webhook_url / .email, each its own Delivery row
    # (recipient tagged e.g. "slack:role:eng_lead"), same idempotency/error-handling
    # pattern as the existing Slack/email blocks above.
```

Alert content (Slack message / email body) explicitly states, in order: **what** happened (entity
+ filing type + domain), **why it matters** (`rationale`, naming the matched watchlist
entity/product/industry signal), **what to do** (`recommended_action`), and the **priority
score**. This directly satisfies the requirement that an engineering-domain filing reaches the
Eng Lead's channel with an explanation of what it affects in their part of the business — not
just a generic "new filing" notice.

Idempotency, per-channel error isolation, and `Delivery` row recording follow the exact patterns
already established for Slack/email in `deliver_node` (one `_record_delivery` call per
channel/role, failures in one role's send never block another's).

## Testing

- **`relevance_agent`**: unit tests mocking the LLM client — schema-valid response; malformed
  JSON retried once then falls back to the neutral default; `org_profile=None` still produces a
  valid `RelevanceResult`; `state.extraction=None` short-circuits to the fallback without calling
  the model (mirrors `analyze_node`'s "no chunks, skip" guard).
- **`compute_priority_score`**: unit tests covering the four `RiskLevel` values crossed with
  `relevance_score` at 0.0/0.5/1.0, confirming the documented equal-weighting behavior.
- **`roles_for_domain`**: unit tests for every `FilingDomain` value including `None` and `OTHER`.
- **`deliver_node` role fan-out**: unit tests mirroring the existing Slack/webhook delivery
  tests — role settings present/absent, multiple roles for one domain (not applicable today since
  each domain maps to exactly one restricted role, but the loop supports it), a role channel send
  failure not blocking the org-wide channels or other roles.
- **Migrations**: upgrade/downgrade/upgrade cycle for `organization_profiles`,
  `organization_role_delivery_settings`, and the three new `filings` columns, against real
  Postgres per this project's established live-verification bar.
- **Live verification**: one real LLM call through `relevance_agent` against a fixture filing +
  a fixture org profile with a deliberately matching watchlist entity, confirming the returned
  `rationale` actually names the match (not just a plausible-sounding generic string).
