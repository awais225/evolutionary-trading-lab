"""Read persisted timestamps only; never interpolate or fabricate candles."""
import math


def timestamps(store, start, end):
    try:
        df = store.read_range(start_ts=start, end_ts=end, columns=['ts'])
    except TypeError:  # in-memory adapters implementing the older no-arg contract
        df = store.read_range()
    values = list(df['ts']) if 'ts' in df.columns else []
    # Test/in-memory stores expose their persisted timestamp set, not received bars.
    if not values and hasattr(store, 'seen'):
        values = list(store.seen)
    return sorted({float(t) for t in values if math.isfinite(float(t)) and start <= float(t) <= end})


def coverage(store, start, end, step):
    ts = timestamps(store, start, end)
    missing, warnings = [], []
    if not ts:
        missing.append({'start': start, 'end': end, 'reason': 'no persisted in-range bars'})
    else:
        if ts[0] > start + 2 * step:
            missing.append({'start': start, 'end': ts[0], 'reason': 'requested head not available; boundary closure not assumed'})
        if ts[-1] < end - 2 * step:
            missing.append({'start': ts[-1], 'end': end, 'reason': 'requested tail not available; boundary closure not assumed'})
        for a, b in zip(ts, ts[1:]):
            if b - a <= step * 1.5:
                continue
            gap = {'start': a, 'end': b, 'seconds': b - a}
            if b - a > 96 * 3600:
                missing.append({**gap, 'reason': 'material internal history gap (>96h)'})
            else:
                warnings.append({**gap, 'reason': 'possible session/market closure or short data gap; calendar not verified'})
    return {'persisted_in_range': len(ts), 'persisted_range_first': ts[0] if ts else None,
            'persisted_range_last': ts[-1] if ts else None, 'uncovered': missing,
            'gap_warning_count': len(warnings), 'gap_warnings': warnings[:25],
            'gap_warnings_truncated': len(warnings) > 25,
            'coverage_policy': 'observed persisted span; internal gaps >96h are material; smaller gaps remain unverified warnings',
            'status': 'PARTIAL' if missing else 'COMPLETE'}


def missing_intervals(report, start, end, step):
    intervals = sorted((max(start, r['start'] - step * 2), min(end, r['end'] + step * 2))
                       for r in report['uncovered'])
    out = []
    for a, b in intervals:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(b, out[-1][1]))
        else:
            out.append((a, b))
    return out
