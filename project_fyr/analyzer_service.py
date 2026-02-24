# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Entry point for the analyzer service."""

from __future__ import annotations

import logging
from .service import AnalyzerService

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    AnalyzerService().start()
