#!/usr/bin/env python3
"""Recompute adapter totals/tables from primary rows and bind their sources."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]


def check(root=ROOT, write=True):
    folder = root/'results/sqlite-adapter'
    summary = json.loads((folder/'summary.json').read_text())
    config = json.loads((root/'scripts/sqlite_experiment.json').read_text())
    if config != summary['config']: raise ValueError('adapter experiment configuration changed')
    for name, expected in summary['sources'].items():
        if hashlib.sha256((root/name).read_bytes()).hexdigest() != expected:
            raise ValueError('adapter result/source mismatch: '+name)
    crashes = [json.loads(s) for s in (folder/'crashes.jsonl').read_text().splitlines()]
    costs = [json.loads(s) for s in (folder/'costs.jsonl').read_text().splitlines()]
    keys = [(r['mode'],r['workload'],r['policy'],r['target'],r['stage']) for r in crashes]
    if len(keys) != len(set(keys)): raise ValueError('duplicate crash cases')
    for mode in config['journal_modes']:
        for family in config['crash_workloads']:
            policies = config['safe_policies'] + ([config['negative_policy']] if family=='replace' else [])
            for policy in policies:
                group = [r for r in crashes if (r['mode'],r['workload'],r['policy'])==(mode,family,policy)]
                targets = sorted({r['target'] for r in group})
                if not targets or targets != list(range(max(targets)+1)): raise ValueError('missing crash target')
                for target in targets:
                    if sorted(r['stage'] for r in group if r['target']==target) != sorted(config['crash_stages']):
                        raise ValueError('incomplete crash-stage coverage')
    safe = [r for r in crashes if r['policy'] in config['safe_policies']]
    negative = [r for r in crashes if r['policy']==config['negative_policy']]
    if not negative or not any(not r['native_ok'] for r in negative): raise ValueError('negative control absent')
    for r in crashes:
        if r['committed_events'] != r['target']+int(r['stage']=='after-commit'): raise ValueError('partial transaction')
        if r['native_model_equal'] is not True or r['integrity']!='ok': raise ValueError('native/database mismatch')
    if not all(r['native_ok'] and r['trace_safe'] and r['client_bytes_checked'] and r['resume_checked'] for r in safe):
        raise ValueError('safe adapter outcome failed')
    expected_costs = {(m,f,s,p) for m in config['journal_modes'] for f in config['cost_families']
                      for s in config['cost_seeds'] for p in config['safe_policies']}
    observed = [(r['mode'],r['family'],r['seed'],r['policy']) for r in costs]
    if len(observed)!=len(set(observed)) or set(observed)!=expected_costs: raise ValueError('cost matrix differs')
    if not all(r['native_model_equal'] and r['operations']==config['operations'] for r in costs): raise ValueError('cost correctness failed')
    if not all(r['sql_restore_checked']==(r['family']=='backup') for r in costs): raise ValueError('SQL restore missing')
    for mode,family,seed,_ in expected_costs:
        pair = [r for r in costs if (r['mode'],r['family'],r['seed'])==(mode,family,seed)]
        if len({r['events'] for r in pair})!=1 or len({r['roots'] for r in pair})!=1:
            raise ValueError('policies used different event counts')
    counts = {'crash_cases':len(crashes),'safe_cases':len(safe),
              'safe_failures':sum(not r['native_ok'] for r in safe),
              'negative_cases':len(negative),'negative_native_failures':sum(not r['native_ok'] for r in negative),
              'native_model_disagreements':0,'resumed_cases':sum(r['resume_checked'] for r in crashes),
              'cost_runs':len(costs),'sql_restore_runs':sum(r['sql_restore_checked'] for r in costs)}
    if any(summary[k]!=v for k,v in counts.items()): raise ValueError('summary/primary count mismatch')
    lines = []
    for group in summary['cost_medians']:
        for policy in config['safe_policies']:
            selected = [r for r in costs if (r['mode'],r['family'],r['policy'])==(group['mode'],group['family'],policy)]
            for k,v in group[policy].items():
                if v != statistics.median(r[k] for r in selected): raise ValueError('median mismatch')
        a,b = (group[p] for p in config['safe_policies'])
        lines.append(f"{group['mode']} / {group['family']} & {a['slots']:.0f}/{b['slots']:.0f} & "
                     f"{a['bytes_after_checkpoint']/1024:.0f}/{b['bytes_after_checkpoint']/1024:.0f} & "
                     f"{a['write_and_read_ns']/1e6:.1f}/{b['write_and_read_ns']/1e6:.1f} \\\\")
    if write:
        header = '\\begin{tabular}{@{}lrrr@{}}\n\\toprule\nMode / workload & Logical slots & Files (KiB) & Lifecycle (ms)'+r'\\'+'\n\\midrule\n'
        (folder/'table.tex').write_text(header+'\n'.join(lines)+'\n\\bottomrule\n\\end{tabular}\n')
        names = {'crash_cases':'SQLCrashCases','safe_cases':'SQLSafeCases','negative_native_failures':'SQLNegativeFailures',
                 'resumed_cases':'SQLResumed','cost_runs':'SQLCostRuns','sql_restore_runs':'SQLRestoreRuns'}
        (folder/'counts.tex').write_text(''.join('\\newcommand{\\'+name+'}{'+str(counts[k])+'}\n' for k,name in names.items()))
        unique = next(g for g in summary['cost_medians'] if g['mode']=='DELETE' and g['family']=='unique')
        with (folder/'counts.tex').open('a') as handle:
            handle.write('\\newcommand{\\SQLVersion}{'+summary['environment']['sqlite']+'}\n')
            for policy,name in [('frontier-reuse','SQLUniqueReuseMs'),('no-reuse','SQLUniqueAppendMs')]:
                handle.write('\\newcommand{\\'+name+'}{'+format(unique[policy]['write_and_read_ns']/1e6,'.1f')+'}\n')
    return counts


if __name__=='__main__': print(json.dumps(check(),sort_keys=True))
