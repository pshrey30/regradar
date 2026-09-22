"""Unit tests for OrganizationProfile.is_complete()."""

import uuid

from regradar.models.organization_profile import OrganizationProfile, is_complete


def _complete_profile() -> OrganizationProfile:
    return OrganizationProfile(
        organization_id=uuid.uuid4(),
        industry="Biotechnology",
        business_description="We manufacture diagnostic devices.",
        watchlist_entities=["Acme Corp"],
        products=["Widget Pro"],
        risk_priorities=["Data privacy"],
    )


def test_is_complete_returns_false_for_none() -> None:
    assert is_complete(None) is False


def test_is_complete_returns_true_when_all_fields_set() -> None:
    assert is_complete(_complete_profile()) is True


def test_is_complete_returns_false_when_industry_missing() -> None:
    profile = _complete_profile()
    profile.industry = None
    assert is_complete(profile) is False


def test_is_complete_returns_false_when_business_description_missing() -> None:
    profile = _complete_profile()
    profile.business_description = None
    assert is_complete(profile) is False


def test_is_complete_returns_false_when_watchlist_entities_empty() -> None:
    profile = _complete_profile()
    profile.watchlist_entities = []
    assert is_complete(profile) is False


def test_is_complete_returns_false_when_products_empty() -> None:
    profile = _complete_profile()
    profile.products = []
    assert is_complete(profile) is False


def test_is_complete_returns_false_when_risk_priorities_empty() -> None:
    profile = _complete_profile()
    profile.risk_priorities = []
    assert is_complete(profile) is False
