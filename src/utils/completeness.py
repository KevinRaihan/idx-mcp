"""Say which fields are missing, rather than returning a quietly holey payload.

A tool that returns its full shape with nulls scattered through it looks like a
successful call. The caller reads `sector_performance: []` as "no sectors moved"
and `bi_rate_pct: null` as "no rate", when both actually mean the source did not
answer. `get_stock_price` already carried a `partial` flag for exactly this
reason; this is that pattern made reusable rather than copied three more times.

Emptiness is reported for containers too: an empty list is a missing answer, not
an answer of "none". A field whose value is legitimately zero is *not* missing.
"""

from typing import Any, Iterable


def _get_path(payload: dict, path: str) -> tuple[bool, Any]:
    """Resolve a dotted path. Returns (found, value)."""
    node: Any = payload
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _is_missing(found: bool, value: Any) -> bool:
    if not found or value is None:
        return True
    # 0 and 0.0 and False are real answers; empty containers are not.
    if isinstance(value, (str, list, tuple, dict, set)) and len(value) == 0:
        return True
    return False


def mark_partial(
    payload: dict,
    fields: Iterable[str],
    reason: str,
    *,
    key: str = "partial",
) -> dict:
    """Annotate ``payload`` with which of ``fields`` came back empty.

    ``fields`` are dotted paths into the payload. ``partial`` is always set, so
    a caller can branch on it without a membership test; ``missing_fields`` and
    ``partial_reason`` appear only when something is actually missing.
    """
    missing = [f for f in fields if _is_missing(*_get_path(payload, f))]
    payload[key] = bool(missing)
    if missing:
        payload["missing_fields"] = missing
        payload["partial_reason"] = reason
    return payload
