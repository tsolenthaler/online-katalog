#!/usr/bin/env python3
"""Convenience wrapper to run the ISBN enrichment command from repository root."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "src" / "command" / "enrich_isbn.py"
    runpy.run_path(str(target), run_name="__main__")
