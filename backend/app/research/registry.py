"""Read-only canonical registry ordering. No qualification/trading decisions."""
import math


def numeric(value):
    if value is None or isinstance(value, bool) or (isinstance(value, str) and not value.strip()):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError, OverflowError):
        return None


def sort_nodes(rows, value, *, descending=False, text=False):
    """Full filtered set, numeric raw values, missing-last, canonical-id tie."""
    def key(row):
        v = value(row)
        v = str(v).casefold() if text and v is not None else numeric(v)
        return (v is None, (v if text else -v if descending else v) if v is not None else 0,
                int(row.get('node_id') or row.get('id') or 0))
    if text and descending:
        present = [r for r in rows if value(r) is not None]
        present.sort(key=lambda r: int(r.get('node_id') or r.get('id') or 0))
        present.sort(key=lambda r: str(value(r)).casefold(), reverse=True)
        return present + sorted((r for r in rows if value(r) is None), key=lambda r: int(r.get('node_id') or 0))
    return sorted(rows, key=key)
