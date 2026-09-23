# Admin CC on Role-Routed Email Alerts — Design

## Context

ORG-11 (built earlier) added role-routed alert delivery to `deliver_node`
(`src/regradar/agents/delivery_agent.py`): a filing's domain determines
which role(s) it routes to via `domain_scope.roles_for_domain()` (e.g.
`FilingDomain.ENGINEERING` → `ApiKeyRole.ENG_LEAD` only), and each
routed role's Slack/email destination comes from
`OrganizationRoleDeliverySettings(organization_id, role)`.

The user wants every role-routed **email** alert to also reach that
organization's Admin, so nothing routed to a single department goes
unseen by the org's admin. Two things the user explicitly confirmed:

- Domain exclusivity (engineering alerts never reaching Analyst/Legal
  Counsel) is **already correct** via the existing `roles_for_domain()`
  routing — no change needed there.
- Batching multiple alerts into one email is **out of scope** for this
  change — deferred, not part of this spec.
- This is **email-only** — Slack is not CC'd to Admin under this spec.

## Design

`OrganizationRoleDeliverySettings` already supports any `ApiKeyRole`
value, including `ApiKeyRole.ADMIN` — no schema change. An org configures
its Admin's email there exactly the same way it configures Eng
Lead/Analyst/Legal Counsel's.

In `deliver_node`'s existing role-routed fan-out loop, immediately after
attempting the routed role's own email send (the existing `if
role_settings.email and (...) not in already_sent_recipients:` block),
add a second, independent attempt: look up
`OrganizationRoleDeliverySettings(filing.organization_id,
ApiKeyRole.ADMIN)` for this filing's organization, and — if that row
exists and has an email configured — send the same `role_message`
content to it too.

This Admin lookup and send is **structurally identical** to the existing
per-role email block (same `send_email_alert` call, same
`_record_delivery` call producing its own `Delivery` row, same
try/except isolating its failure from the role recipient's), just
targeting a different, fixed role (`ApiKeyRole.ADMIN`) instead of the
domain-routed one. Its dedup key is `(DeliveryChannel.EMAIL,
admin_settings.email)`, matching the existing convention of using the
real destination address as the recipient-dedup key for email.

**Placement:** only sent when the routed role loop actually has a
role to send to in the first place — i.e., inside the existing `for role
in roles_for_domain(state.domain):` loop, not as a separate top-level
step. A filing with no routed role (`FilingDomain.OTHER`, or `None`
domain) still sends nothing extra to Admin, consistent with "Admin
copies what got routed" rather than "Admin gets everything unconditionally."

**Not sent when:**
- No role is returned by `roles_for_domain(state.domain)` (nothing was
  routed in the first place).
- The org has no `OrganizationRoleDeliverySettings` row for
  `ApiKeyRole.ADMIN`, or that row has no `email` set — same graceful
  "not configured, skip silently" pattern already used everywhere else
  in this file (e.g. `statuses.append(f"...=not_configured")` is not
  needed here since this is an *additive* copy, not a required channel).
- The exact same Admin email already received THIS filing's alert via
  some other path in the same run (guarded by the existing
  `already_sent_recipients` dedup set, same as every other channel).

**Self-CC edge case:** if an org's Admin email happens to be configured
identically to a routed role's email (e.g. one person holds both roles
in a small org), the dedup-by-recipient-address check means only one
copy is sent, not two — this falls out of the existing dedup mechanism
for free, no special-casing needed.

## Testing

- New unit test: a filing with `FilingDomain.ENGINEERING`,
  `OrganizationRoleDeliverySettings` configured for both `ENG_LEAD` and
  `ADMIN` with distinct emails → both receive the alert, as two separate
  `Delivery` rows.
- New unit test: same setup but no `ADMIN` row configured → only the
  Eng Lead receives it, no error, no extra `Delivery` row attempted.
- New unit test: `ADMIN` row configured with the *same* email address as
  the routed role → only one send attempt (dedup), not two.
- New unit test: `FilingDomain.OTHER` (no routed role) → Admin receives
  nothing, even if an `ADMIN` row exists.
