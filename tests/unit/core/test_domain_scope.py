"""Tests for the role-scoped dashboard's role -> domain mapping."""

from regradar.core.domain_scope import allowed_domains_for_role, is_domain_visible_to_role
from regradar.models.enums import ApiKeyRole, FilingDomain


def test_admin_and_executive_are_unrestricted():
    assert allowed_domains_for_role(ApiKeyRole.ADMIN) is None
    assert allowed_domains_for_role(ApiKeyRole.EXECUTIVE) is None


def test_analyst_is_restricted_to_financial():
    assert allowed_domains_for_role(ApiKeyRole.ANALYST) == {FilingDomain.FINANCIAL}


def test_eng_lead_is_restricted_to_engineering():
    assert allowed_domains_for_role(ApiKeyRole.ENG_LEAD) == {FilingDomain.ENGINEERING}


def test_legal_counsel_is_restricted_to_clinical_and_environmental():
    assert allowed_domains_for_role(ApiKeyRole.LEGAL_COUNSEL) == {
        FilingDomain.CLINICAL,
        FilingDomain.ENVIRONMENTAL,
    }


def test_is_domain_visible_true_for_unrestricted_role_even_with_none_domain():
    assert is_domain_visible_to_role(None, ApiKeyRole.ADMIN) is True
    assert is_domain_visible_to_role(FilingDomain.ENGINEERING, ApiKeyRole.EXECUTIVE) is True


def test_is_domain_visible_false_for_none_domain_on_restricted_role():
    """An unclassified filing (domain=None) has nothing to match a
    restricted role's allowed set against, so it stays invisible until
    classification actually assigns it a domain."""
    assert is_domain_visible_to_role(None, ApiKeyRole.ENG_LEAD) is False


def test_is_domain_visible_matches_restricted_role_correctly():
    assert is_domain_visible_to_role(FilingDomain.ENGINEERING, ApiKeyRole.ENG_LEAD) is True
    assert is_domain_visible_to_role(FilingDomain.FINANCIAL, ApiKeyRole.ENG_LEAD) is False
    assert is_domain_visible_to_role(FilingDomain.CLINICAL, ApiKeyRole.LEGAL_COUNSEL) is True
    assert is_domain_visible_to_role(FilingDomain.ENVIRONMENTAL, ApiKeyRole.LEGAL_COUNSEL) is True
    assert is_domain_visible_to_role(FilingDomain.FINANCIAL, ApiKeyRole.LEGAL_COUNSEL) is False
