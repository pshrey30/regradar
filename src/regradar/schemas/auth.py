"""Request bodies for FE-02's email/password signup and login."""

import re

from pydantic import BaseModel, Field, field_validator

from regradar.models.enums import ApiKeyRole

# A pragmatic format check, not full RFC 5322 validation — this project
# has no email-confirmation flow, so "deliverable" is never actually
# verified either way; email-validator wasn't added as a dependency for
# a check this deliberately shallow.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Every role a signing-up person may choose for themselves — deliberately
# excludes Admin. Self-service role escalation is never allowed anywhere
# in this app (see api/routers/api_keys.py's PATCH /v1/api-keys/{id} for
# the one place roles *can* change, which is Admin-only and never the
# caller's own choice); an Admin account is created either by another
# Admin issuing an Admin-role invite into an EXISTING org, or by
# POST /v1/auth/signup-org, which creates a brand-new org with no invite
# at all (see auth.py's signup_org()).
SELF_SELECTABLE_ROLES = frozenset(
    {ApiKeyRole.ANALYST, ApiKeyRole.EXECUTIVE, ApiKeyRole.LEGAL_COUNSEL, ApiKeyRole.ENG_LEAD}
)


def _normalize_and_validate_email(value: str) -> str:
    if not _EMAIL_PATTERN.match(value):
        raise ValueError("Not a valid email address")
    return value.lower()


class SignupRequest(BaseModel):
    email: str
    password: str
    invite_code: str
    role: ApiKeyRole
    display_name: str | None = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        return _normalize_and_validate_email(value)

    @field_validator("role")
    @classmethod
    def _validate_self_selectable_role(cls, value: ApiKeyRole) -> ApiKeyRole:
        if value not in SELF_SELECTABLE_ROLES:
            raise ValueError(
                "Not a role you can sign up as — an Admin account is granted by "
                "another Admin, never chosen at signup."
            )
        return value


class SignupOrgRequest(BaseModel):
    """The no-invite counterpart to SignupRequest: creates a brand-new
    Organization and its first Admin account together. No `role` or
    `invite_code` field — both are implied (Admin, no invite required)."""

    org_name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        return _normalize_and_validate_email(value)


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.lower()


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
