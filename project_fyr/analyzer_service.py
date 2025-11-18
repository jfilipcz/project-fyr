"""Standalone entrypoint for the Project Fyr analyzer."""

from __future__ import annotations

from .service import run_analyzer


def main():
    run_analyzer()


if __name__ == "__main__":  # pragma: no cover
    main()
