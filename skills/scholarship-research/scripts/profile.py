#!/usr/bin/env python3
"""Backward-compatible CLI wrapper for scholarship profile management."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scholarship_profile import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
