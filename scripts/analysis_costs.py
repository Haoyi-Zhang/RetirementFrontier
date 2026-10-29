"""Bounded paired CPU-cost observations for the current analysis routines."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from checker import analyze_model, pair_analyze_model
from engine import build_trace, scaling_operations, strip_build_metrics
from model import admit_trace
from verifier import verify_certificate

CONFIG = {'families': ['shared', 'unique', 'churn'], 'sizes': [32, 128, 512],
          'seeds': list(range(20000, 20005)), 'repetitions': 8, 'warmups': 1,
          'batch_size': 4, 'chunk_bytes': 256, 'minimum_witness_search': False,
          'deadline_seconds': 600, 'target_batch_cpu_ns': 20000000, 'maximum_batch_calls': 16384}
METHODS = ('admission', 'frontier', 'pair', 'consumer')
OUT = ROOT / 'results/analysis-costs'


def bindings():
    names = ['src/checker.py', 'src/model.py', 'src/engine.py', 'src/verifier.py',
             'src/strictjson.py', 'scripts/analysis_costs.py']
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}


def case_statistics(row):
    samples = row['samples_cpu_ns']
    if set(samples) != set(METHODS):
        raise ValueError('Unexpected timing methods')
    for values in samples.values():
        if len(values) != CONFIG['repetitions']:
            raise ValueError('Expected eight CPU batches')
        for value in values:
            if (set(value) != {'cpu_ns', 'calls'} or type(value['cpu_ns']) is not int or value['cpu_ns'] <= 0
                    or type(value['calls']) is not int or not 1 <= value['calls'] <= CONFIG['maximum_batch_calls']):
                raise ValueError('Expected positive measured CPU time and bounded call count')
    medians = {name: statistics.median(v['cpu_ns'] / v['calls'] for v in samples[name]) for name in METHODS}
    return {**medians, 'pair_over_frontier': medians['pair'] / medians['frontier']}


def aggregates(rows):
    expected = {(f, n, s) for f in CONFIG['families'] for n in CONFIG['sizes'] for s in CONFIG['seeds']}
    keys = [(r['family'], r['operations'], r['seed']) for r in rows]
    if len(keys) != len(expected) or set(keys) != expected:
        raise ValueError('Missing, duplicated or unexpected analysis-cost case')
    result = []
    for family in CONFIG['families']:
        for size in CONFIG['sizes']:
            group = [r for r in rows if (r['family'], r['operations']) == (family, size)]
            stats = [case_statistics(r) for r in group]
            if not all(r['safe'] and r['consumer_accepted'] and r['decisions_equal'] for r in group):
                raise ValueError('A timed result disagrees with its checked decision')
            result.append({'family': family, 'operations': size,
                           **{name + '_ms': statistics.median(s[name] for s in stats) / 1e6 for name in METHODS},
                           'paired_ratio_median': statistics.median(s['pair_over_frontier'] for s in stats),
                           'paired_ratio_min': min(s['pair_over_frontier'] for s in stats),
                           'paired_ratio_max': max(s['pair_over_frontier'] for s in stats)})
    return result


def experiment():
    if sys.platform != 'linux':
        raise RuntimeError('Run the timing campaign on the documented Linux host')
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (int(3.5 * 1024**3), int(3.5 * 1024**3)))
    resource.setrlimit(resource.RLIMIT_CPU, (CONFIG['deadline_seconds'], CONFIG['deadline_seconds']))
    available = sorted(os.sched_getaffinity(0))
    os.sched_setaffinity(0, {available[0]})
    started = time.monotonic()
    source = bindings()
    rows = []
    for family in CONFIG['families']:
        for size in CONFIG['sizes']:
            for seed in CONFIG['seeds']:
                trace = strip_build_metrics(build_trace(scaling_operations(size, family, seed, chunk_size=CONFIG['chunk_bytes']),
                                                       deduplicate=True, batch_size=CONFIG['batch_size']))
                trace_bytes = json.dumps(trace, sort_keys=True, separators=(',', ':')).encode()
                model = admit_trace(trace)
                front, pair = analyze_model(model), pair_analyze_model(model)
                if not front['safe'] or front['safe'] != pair['safe']:
                    raise RuntimeError('Expected matched safe generated histories')
                certificate = front['certificate']
                calls = {'admission': lambda: admit_trace(trace), 'frontier': lambda: analyze_model(model),
                         'pair': lambda: pair_analyze_model(model),
                         'consumer': lambda: verify_certificate(trace, certificate)}
                def check(name, value):
                    if name == 'admission':
                        if value.events != model.events or value.deps != model.deps:
                            raise RuntimeError('Admission changed the input model')
                    elif name == 'consumer':
                        if not value['accepted']:
                            raise RuntimeError('Consumer rejected a timed certificate')
                    elif not value['safe']:
                        raise RuntimeError('Timed analysis changed its decision')
                for _ in range(CONFIG['warmups']):
                    for name in METHODS:
                        check(name, calls[name]())
                batches = {}
                def timed_batch(name, count):
                    start = time.process_time_ns()
                    for _ in range(count):
                        value = calls[name]()
                    elapsed = time.process_time_ns() - start
                    check(name, value)
                    return elapsed
                for name in METHODS:
                    count = 1
                    while timed_batch(name, count) < CONFIG['target_batch_cpu_ns']:
                        count *= 2
                        if count > CONFIG['maximum_batch_calls']:
                            raise RuntimeError('CPU clock cannot resolve the configured batch')
                    batches[name] = count
                samples = {name: [] for name in METHODS}
                orders = []
                for repeat in range(CONFIG['repetitions']):
                    offset = (len(rows) + repeat) % len(METHODS)
                    order = METHODS[offset:] + METHODS[:offset]
                    orders.append(list(order))
                    for name in order:
                        elapsed = timed_batch(name, batches[name])
                        samples[name].append({'cpu_ns': elapsed, 'calls': batches[name]})
                    if time.monotonic() - started > CONFIG['deadline_seconds']:
                        raise TimeoutError('Analysis-cost deadline exceeded')
                if trace_bytes != json.dumps(trace, sort_keys=True, separators=(',', ':')).encode():
                    raise RuntimeError('A timed routine mutated the trace')
                rows.append({'family': family, 'operations': size, 'seed': seed, 'events': model.n,
                             'roots': len(model.roots), 'trace_sha256': hashlib.sha256(trace_bytes).hexdigest(),
                             'safe': True, 'consumer_accepted': True, 'decisions_equal': True,
                             'frontier_obligations': front['frontier_obligations'],
                             'pair_obligations': pair['pair_obligations'], 'certificate_bytes': front['certificate_bytes'],
                             'orders': orders, 'samples_cpu_ns': samples})
                print(f'{family} {size} seed {seed}: complete', flush=True)
    if source != bindings():
        raise RuntimeError('Sources changed during timing')
    cpu_model = next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                      if line.startswith('model name')), platform.processor())
    summary = {'config': CONFIG, 'sources': source, 'cases': len(rows),
               'timed_blocks': len(rows) * CONFIG['repetitions'] * len(METHODS),
               'timed_calls': sum(v['calls'] for r in rows for values in r['samples_cpu_ns'].values() for v in values),
               'clock': 'process_time_ns', 'affinity_cpu_count': 1,
               'host': {'system': platform.system(), 'release': platform.release(),
                        'machine': platform.machine(), 'python': platform.python_version(), 'cpu_model': cpu_model},
               'aggregates': aggregates(rows),
               'scope': 'Controlled retained scaling inputs, not production traces. Admission is separate. Frontier includes certificate construction and JSON serialization; pair returns a decision and diagnostics. Consumer reparses the original trace. No disk, recovery, minimum search, or all-workload speedup claim.'}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'observations.jsonl').write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows), encoding='utf-8')
    (OUT / 'summary.json').write_text(json.dumps(summary, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return summary


def verify():
    summary = json.loads((OUT / 'summary.json').read_text(encoding='utf-8'))
    rows = [json.loads(line) for line in (OUT / 'observations.jsonl').read_text(encoding='utf-8').splitlines()]
    if summary['config'] != CONFIG or summary['sources'] != bindings():
        raise ValueError('Analysis-cost input/source binding changed')
    if summary['aggregates'] != aggregates(rows) or summary['cases'] != len(rows):
        raise ValueError('Analysis-cost aggregates do not match primary rows')
    total_calls = sum(v['calls'] for r in rows for values in r['samples_cpu_ns'].values() for v in values)
    if (summary['timed_blocks'] != 1440 or summary['timed_calls'] != total_calls
            or summary['clock'] != 'process_time_ns' or summary['affinity_cpu_count'] != 1):
        raise ValueError('Unexpected timing protocol')
    for index, row in enumerate(rows):
        expected_order = [list(METHODS[(index+r) % 4:] + METHODS[:(index+r) % 4]) for r in range(8)]
        if row['orders'] != expected_order:
            raise ValueError('Unexpected counterbalanced timing order')
    return summary


def table(summary, target):
    lines = [r'\begin{tabular}{@{}lrrrrr@{}}', r'\toprule',
             r'Family & Ops & Admission & Frontier & Pair & Consumer \\', r'\midrule']
    for row in summary['aggregates']:
        cells = [row['family'].title(), str(row['operations'])] + [f"{row[name + '_ms']:.3f}" for name in METHODS]
        lines.append(' & '.join(cells) + r' \\')
    lines += [r'\bottomrule', r'\end{tabular}']
    target.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--run', action='store_true')
    mode.add_argument('--verify', action='store_true')
    parser.add_argument('--table', type=Path)
    args = parser.parse_args()
    if args.run:
        experiment()
    summary = verify()
    if args.table:
        table(summary, args.table)
    print(json.dumps({'cases': summary['cases'], 'timed_blocks': summary['timed_blocks'],
                      'timed_calls': summary['timed_calls'], 'verified': True}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
