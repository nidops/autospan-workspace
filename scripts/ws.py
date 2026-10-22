#!/usr/bin/env python3
"""Entry point that works without installing the local workspace package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aworkspace.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
