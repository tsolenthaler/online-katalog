#!/usr/bin/env python3
"""Convenience wrapper to run Google-Books-based ISBN fill from repository root."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "src" / "command" / "fill_missing_from_google_books.py"
    runpy.run_path(str(target), run_name="__main__")
