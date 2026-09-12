"""Pydantic request/response models for /v1/invites.

An invite is purely a signup gate, not a role grant — it has no request
body fields at all; see schemas/auth.py's SELF_SELECTABLE_ROLES for where
the role actually gets chosen (by the person signing up, restricted to
non-Admin roles).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel


class InviteResponse(BaseModel):
    """Never carries the plaintext code — used for every response except
    the one right after creation."""

    id: uuid.UUID
    code_suffix: str | None
    used_at: datetime | None
    used_by_email: str | None
    created_at: datetime


class InviteCreateResponse(InviteResponse):
    """The one and only response that includes the plaintext code —
    shown exactly once, at creation."""

    code: str
