"""Request bodies for FE-02's email/password signup and login."""

import re

from pydantic import BaseModel, field_validator

# A pragmatic format check, not full RFC 5322 validation — this project
# has no email-confirmation flow, so "deliverable" is never actually
# verified either way; email-validator wasn't added as a dependency for
# a check this deliberately shallow.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupRequest(BaseModel):
    email: str
    password: str
    display_name: str | None = None

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        if not _EMAIL_PATTERN.match(value):
            raise ValueError("Not a valid email address")
        return value.lower()


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return value.lower()
