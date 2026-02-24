# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Project Fyr Contributors
"""Standalone entrypoint for the Project Fyr watcher."""

from __future__ import annotations

import logging
from .service import run_watcher


def main():
    logging.basicConfig(level=logging.DEBUG)
    run_watcher()


if __name__ == "__main__":  # pragma: no cover
    main()
