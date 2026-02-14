"""Standalone entry point for idx-mcp server.

This script adds the project root to sys.path so the server can be launched
from any working directory without relying on `python -m` or `cwd`.
"""

import sys
from pathlib import Path

# Ensure the project root is on sys.path
project_root = Path(__file__).resolve().parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.server import main

if __name__ == "__main__":
    main()
