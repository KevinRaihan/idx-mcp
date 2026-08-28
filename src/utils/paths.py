"""Filesystem locations used by the server.

Everything the server writes lives under a single user-owned data directory so
the package itself stays read-only and can be installed anywhere (system venv,
uv tool, pipx). Override with the ``IDX_MCP_HOME`` environment variable.
"""

import os
from pathlib import Path


def data_home() -> Path:
    """Root directory for all server-written state."""
    override = os.environ.get("IDX_MCP_HOME")
    return Path(override).expanduser() if override else Path.home() / ".idx-mcp"


def log_dir() -> Path:
    """Directory for the error log and the predictions log. Created on demand."""
    path = data_home() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def predictions_log_file() -> Path:
    return log_dir() / "predictions_log.json"


def error_log_file() -> Path:
    return log_dir() / "error.log"
