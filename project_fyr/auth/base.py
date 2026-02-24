# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Base authentication provider interface."""

from abc import ABC, abstractmethod
from typing import Dict


class AuthProvider(ABC):
    """Abstract base class for authentication providers."""

    @abstractmethod
    async def validate_token(self, token: str) -> Dict:
        """
        Validate an authentication token.

        Args:
            token: The authentication token to validate

        Returns:
            Dict with user info: {
                "user_id": str,
                "email": str,
                "name": str,
                "roles": List[str],
                "provider": str
            }

        Raises:
            HTTPException(401) if validation fails
        """
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider name (e.g., 'entra', 'okta', 'local')."""
        pass
