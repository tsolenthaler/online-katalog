#!/usr/bin/env python3
"""Convenience wrapper to run the catalog merge command from repository root."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "src" / "command" / "merge_catalogs.py"
    runpy.run_path(str(target), run_name="__main__")