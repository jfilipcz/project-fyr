# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Providers package for authentication."""

from .entra import EntraIDProvider
from .oidc import GenericOIDCProvider

__all__ = ["EntraIDProvider", "GenericOIDCProvider"]
