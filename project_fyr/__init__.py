# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Project Fyr package."""

from datetime import datetime, timezone

from .config import Settings
from .models import Analysis, ReducedContext, RawContext

__all__ = ["Settings", "RawContext", "ReducedContext", "Analysis", "utcnow"]


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime.

    Drop-in replacement for the deprecated ``datetime.utcnow()``.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
