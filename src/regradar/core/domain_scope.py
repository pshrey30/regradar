"""Role-scoped dashboard: which FilingDomain(s) each role's API key can see.

Every non-Admin role is locked to its own domain(s) — an Eng Lead's
dashboard (filings list, filing detail, search, and the Activity/alerts
feed) only ever shows Engineering-domain content, never Financial or
Clinical, for example. Admin is always unrestricted, matching how Admin
already behaves everywhere else in this app. Executive stays domain-
unrestricted too (it already has its own separate risk-level restriction
— see filings.py's `_EXECUTIVE_ALLOWED_RISK_LEVELS` — cutting it down to
one domain on top of that would leave it seeing almost nothing).

`None` means unrestricted; a `frozenset` means "only these domains."
"""

from regradar.models.enums import ApiKeyRole, FilingDomain

ROLE_DOMAIN_RESTRICTIONS: dict[ApiKeyRole, frozenset[FilingDomain] | None] = {
    ApiKeyRole.ADMIN: None,
    ApiKeyRole.ANALYST: frozenset({FilingDomain.FINANCIAL}),
    ApiKeyRole.EXECUTIVE: None,
    ApiKeyRole.LEGAL_COUNSEL: frozenset({FilingDomain.CLINICAL, FilingDomain.ENVIRONMENTAL}),
    ApiKeyRole.ENG_LEAD: frozenset({FilingDomain.ENGINEERING}),
}


def allowed_domains_for_role(role: ApiKeyRole) -> frozenset[FilingDomain] | None:
    """None means every domain is visible; otherwise the exact allowed set."""
    return ROLE_DOMAIN_RESTRICTIONS.get(role)


def is_domain_visible_to_role(domain: FilingDomain | None, role: ApiKeyRole) -> bool:
    """A filing with domain=None (not yet classified) is visible to nobody
    restricted to a specific domain — there's nothing to match against —
    but stays visible to unrestricted roles (Admin/Executive), same as
    before this feature existed."""
    allowed = allowed_domains_for_role(role)
    if allowed is None:
        return True
    return domain is not None and domain in allowed
