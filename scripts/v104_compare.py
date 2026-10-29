"""Compare isolated V10.4 benchmark files; exit nonzero on changed outcomes."""
import argparse
import json
from pathlib import Path
from statistics import median


def compare(before, after):
    assert before['settings'] == after['settings'], 'Settings differ'
    assert len(before['generation']) == len(after['generation']), 'Round count differs'
    for a, b in zip(before['generation'], after['generation']):
        for key in ('nodes', 'record_digest', 'rng_digest'):
            assert a[key] == b[key], 'Generation differs: ' + key
    assert len(before['deeptest']) == len(after['deeptest']), 'Batch count differs'
    for a, b in zip(before['deeptest'], after['deeptest']):
        for key in ('requested_workers', 'effective_workers', 'round', 'scenarios', 'partial', 'failed'):
            assert a[key] == b[key], 'Batch differs: ' + key
        assert len(a['tasks']) == len(b['tasks']), 'Task count differs'
        for x, y in zip(a['tasks'], b['tasks']):
            for key in ('trades', 'trade_digest', 'metrics_digest'):
                assert x[key] == y[key], 'DeepTest differs: ' + key
    x, y = [median(g['nodes_per_minute'] for g in r['generation']) for r in (before, after)]
    result = {'correctness': 'MATCH settings, nodes, identities/genomes, RNG, full trades and non-observational metrics',
              'generation': {'before_nodes_per_minute': x, 'after_nodes_per_minute': y, 'gain_percent': 100*(y/x-1)},
              'deeptest': []}
    for workers in sorted({d['requested_workers'] for d in before['deeptest']}):
        a, b = [[d for d in r['deeptest'] if d['requested_workers'] == workers] for r in (before, after)]
        x, y = [median(d['total_seconds'] for d in batches) for batches in (a, b)]
        result['deeptest'].append({'requested_workers': workers, 'effective_workers': b[0]['effective_workers'],
                                  'before_seconds': x, 'after_seconds': y, 'speedup': x/y,
                                  'after_scenarios_per_minute': 240/y})
    return result


if __name__ == '__main__':
    if not __debug__:raise RuntimeError('Run without Python optimization; correctness assertions are required')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    args = parser.parse_args()
    print(json.dumps(compare(json.loads(args.before.read_text()), json.loads(args.after.read_text())), indent=2))
