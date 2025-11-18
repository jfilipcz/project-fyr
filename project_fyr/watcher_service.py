"""Standalone entrypoint for the Project Fyr watcher."""

from __future__ import annotations

from .service import run_watcher


def main():
    run_watcher()


if __name__ == "__main__":  # pragma: no cover
    main()
