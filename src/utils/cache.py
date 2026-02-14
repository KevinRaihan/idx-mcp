"""In-memory TTL cache for reducing redundant requests."""

import hashlib
import json
import time
from typing import Any


class TTLCache:
    """Simple in-memory cache with per-key TTL expiration."""

    # Default TTLs in seconds
    TTL_PRICE = 300       # 5 minutes
    TTL_FUNDAMENTALS = 3600  # 1 hour
    TTL_PROFILE = 3600    # 1 hour
    TTL_NEWS = 900        # 15 minutes
    TTL_TECHNICALS = 300  # 5 minutes
    TTL_BROKER = 300      # 5 minutes
    TTL_FLOW = 300        # 5 minutes
    TTL_MARKET = 300      # 5 minutes

    def __init__(self):
        self._store: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _make_key(tool_name: str, ticker: str, params: dict | None = None) -> str:
        params_str = json.dumps(params or {}, sort_keys=True)
        params_hash = hashlib.md5(params_str.encode()).hexdigest()[:8]
        return f"{tool_name}:{ticker}:{params_hash}"

    def get(self, tool_name: str, ticker: str, params: dict | None = None) -> Any | None:
        key = self._make_key(tool_name, ticker, params)
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.time() > entry["expires_at"]:
            del self._store[key]
            return None
        return entry["value"]

    def set(self, tool_name: str, ticker: str, value: Any, ttl: int, params: dict | None = None):
        key = self._make_key(tool_name, ticker, params)
        self._store[key] = {
            "value": value,
            "expires_at": time.time() + ttl,
        }

    def clear(self):
        self._store.clear()


# Global cache instance
cache = TTLCache()
