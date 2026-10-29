#!/usr/bin/env python3
"""Bounded real-engine crash, byte-recovery and matched-cost campaign."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from sqlite_store import SQLiteStore, canonical
from model import admit_trace
from checker import analyze_trace
from medium import materialize, recover
from verifier import verify_certificate

EXIT = 86
CONFIG = ROOT/'scripts/sqlite_experiment.json'


def operations(family, count=4, seed=0, chunk=256):
    rng = random.Random(seed)
    def put(name, payload):
        return {'op': 'put', 'name': name, 'chunks_hex': [payload[i:i+chunk].hex() for i in range(0,len(payload),chunk)]}
    if family == 'replace':
        return [put('object', b'A'*32), put('object', b'B'*32), put('object', b'C'*32)]
    if family == 'alias-delete':
        return [put('a', b'shared'), put('b', b'shared'), {'op':'delete','name':'a'}, put('b', b'new')]
    rows = []
    for i in range(count):
        if family == 'churn' and i % 5 == 4:
            rows.append({'op': 'delete', 'name': str((i-2)%4)})
            continue
        name = str(i % (2 if family == 'shared' else 4))
        payload = (bytes([65+i%3])*chunk*2 if family == 'shared'
                   else rng.randbytes(chunk*2))
        rows.append(put(name, payload))
    return rows


def backup_operations(count, chunk):
    """Real SQLite backup images; a controlled evolving SQL workload."""
    result = []
    with tempfile.TemporaryDirectory(prefix='rf-source-db-') as tmp:
        source = sqlite3.connect(Path(tmp)/'source.db')
        source.execute('CREATE TABLE accounts(id INTEGER PRIMARY KEY, balance INTEGER NOT NULL)')
        source.executemany('INSERT INTO accounts VALUES(?,?)', [(i,100+i) for i in range(12)])
        source.commit()
        for i in range(count):
            source.execute('UPDATE accounts SET balance=balance+? WHERE id=?', (i+1, i%12))
            source.commit()
            image_path = Path(tmp)/f'image-{i}.db'
            with sqlite3.connect(image_path) as image: source.backup(image)
            data = image_path.read_bytes()
            result.append({'op':'put','name':'latest.db','chunks_hex':[data[j:j+chunk].hex() for j in range(0,len(data),chunk)]})
        source.close()
    return result


def expected_at(ops, prefix):
    state = {}
    for op in ops[:prefix]:
        if op['op'] == 'delete': state.pop(op['name'], None)
        else: state[op['name']] = ''.join(op['chunks_hex'])
    return state


def check_views(store, ops):
    native = store.recover()
    trace = store.trace()
    model = admit_trace(trace)
    abstract = recover(materialize(model, range(model.n)))
    if native['ok'] != abstract['ok']:
        raise AssertionError('native/model recovery disagreement')
    if native['ok']:
        for field in ('prefix','root_event','objects_hex'):
            if native[field] != abstract[field]: raise AssertionError('native/model '+field)
        if native['objects_hex'] != expected_at(ops, native['prefix']):
            raise AssertionError('native bytes differ from client-intent oracle')
    return native, trace


def source_binding():
    paths = [ROOT/'src/sqlite_store.py', ROOT/'scripts/run_sqlite_adapter.py', CONFIG,
             ROOT/'src/model.py', ROOT/'src/checker.py', ROOT/'src/medium.py', ROOT/'src/verifier.py',
             ROOT/'src/strictjson.py']
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def child(args):
    ops = json.loads(args.input.read_text())
    def hook(event, stage):
        if event == args.event and stage == args.stage: os._exit(EXIT)
    with SQLiteStore(args.database, mode=args.mode, policy=args.policy, hook=hook) as store:
        for op in ops: store.apply(op)


def crash_campaign(config, out):
    rows = []
    for mode in config['journal_modes']:
        for family in config['crash_workloads']:
            ops = operations(family)
            policies = config['safe_policies'] + ([config['negative_policy']] if family == 'replace' else [])
            for policy in policies:
                with tempfile.TemporaryDirectory(prefix='rf-crash-') as tmp:
                    tmp = Path(tmp); inp = tmp/'intent.json'; inp.write_text(canonical(ops))
                    # A completed dry run determines every issued event boundary.
                    with SQLiteStore(tmp/'dry.db', mode=mode, policy=policy) as store:
                        for op in ops: store.apply(op)
                        full = store.trace()
                    for target in range(len(full['events'])):
                        for stage in config['crash_stages']:
                            with tempfile.TemporaryDirectory(prefix='image-', dir=tmp) as case:
                                database = Path(case)/'store.db'
                                cp = subprocess.run([sys.executable,'-B',str(Path(__file__).resolve()),'--child',
                                    '--database',str(database),'--input',str(inp),'--mode',mode,
                                    '--policy',policy,'--event',str(target),'--stage',stage],
                                    capture_output=True, text=True, timeout=config['child_timeout_seconds'])
                                if cp.returncode != EXIT:
                                    raise AssertionError(f'child did not reach boundary: {cp.returncode}: {cp.stderr[-1000:]}')
                                with SQLiteStore(database, mode=mode, policy=policy) as store:
                                    native, trace = check_views(store, ops)
                                    expected_events = target + int(stage == 'after-commit')
                                    if len(trace['events']) != expected_events:
                                        raise AssertionError('SQLite state/audit transaction was not atomic')
                                    integrity = store.db.execute('PRAGMA integrity_check').fetchone()[0]
                                    if integrity != 'ok': raise AssertionError('SQLite integrity failure')
                                    analysis = analyze_trace(trace)
                                    if policy != config['negative_policy']:
                                        if not native['ok'] or not analysis['safe']:
                                            raise AssertionError('safe policy failed')
                                        verify_certificate(trace, analysis['certificate'])
                                        # Continue after a partial operation, including orphan DATA.
                                        store.put('resumed', b'after-reopen')
                                        if store.get('resumed') != b'after-reopen': raise AssertionError('resume')
                                        if not analyze_trace(store.trace())['safe']: raise AssertionError('resumed trace unsafe')
                                    row = {'mode':mode,'workload':family,'policy':policy,'target':target,'stage':stage,
                                           'committed_events':len(trace['events']), 'native_ok':native['ok'],
                                           'prefix':native.get('prefix'), 'native_model_equal':True,
                                           'client_bytes_checked':native['ok'], 'integrity':integrity,
                                           'trace_safe':analysis['safe'], 'resume_checked':policy != config['negative_policy']}
                                    rows.append(row)
                print(canonical({'crash_group': [mode,family,policy], 'cumulative_cases':len(rows)}), flush=True)
    (out/'crashes.jsonl').write_text(''.join(canonical(r)+'\n' for r in rows))
    return rows


def cost_campaign(config, out):
    rows = []
    backup = backup_operations(config['operations'], config['chunk_bytes'])
    for mode in config['journal_modes']:
        for family in config['cost_families']:
            for seed in config['cost_seeds']:
                ops = backup if family == 'backup' else operations(family, config['operations'], seed, config['chunk_bytes'])
                # Alternating order reduces fixed ordering bias; both paths use
                # the exact same client intents, chunking, durability and audits.
                order = list(config['safe_policies'])
                if seed % 2: order.reverse()
                for policy in order:
                    with tempfile.TemporaryDirectory(prefix='rf-cost-') as tmp:
                        path = Path(tmp)/'store.db'
                        start = time.perf_counter_ns()
                        with SQLiteStore(path, mode=mode, policy=policy) as store:
                            for index, op in enumerate(ops, 1):
                                store.apply(op)
                                if store.recover()['objects_hex'] != expected_at(ops,index): raise AssertionError('cost bytes')
                            write_ns = time.perf_counter_ns()-start
                            native, trace = check_views(store, ops)
                            start = time.perf_counter_ns(); analysis = analyze_trace(trace)
                            verify_certificate(trace, analysis['certificate'])
                            checking_ns = time.perf_counter_ns()-start
                            if not analysis['safe']: raise AssertionError('cost trace unsafe')
                            metrics = store.metrics()
                            before_checkpoint = sum(p.stat().st_size for p in Path(tmp).glob('store.db*'))
                            store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                            after_checkpoint = sum(p.stat().st_size for p in Path(tmp).glob('store.db*'))
                        start = time.perf_counter_ns()
                        with SQLiteStore(path, mode=mode) as store:
                            reopened = store.recover()
                            if reopened['objects_hex'] != native['objects_hex']: raise AssertionError('reopen mismatch')
                            reopen_ns = time.perf_counter_ns()-start
                            sql_restore = False
                            if family == 'backup':
                                restored = Path(tmp)/'restored.db'
                                restored.write_bytes(store.get('latest.db'))
                                with sqlite3.connect(restored) as con:
                                    if con.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise AssertionError('restored SQL')
                                    result = con.execute('SELECT count(*),sum(balance) FROM accounts').fetchone()
                                    expected = (12, sum(100+i for i in range(12))+sum(range(1,len(ops)+1)))
                                    if result != expected: raise AssertionError('restored SQL result')
                                    sql_restore = True
                        rows.append({'mode':mode,'family':family,'seed':seed,'policy':policy,
                                     'operations':len(ops),'write_and_read_ns':write_ns,'checking_ns':checking_ns,
                                     'reopen_ns':reopen_ns, 'bytes_before_checkpoint':before_checkpoint,
                                     'bytes_after_checkpoint':after_checkpoint, 'native_model_equal':True,
                                     'sql_restore_checked':sql_restore, **metrics})
            print(canonical({'cost_group':[mode,family], 'cumulative_runs':len(rows)}),flush=True)
    (out/'costs.jsonl').write_text(''.join(canonical(r)+'\n' for r in rows))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, default=ROOT/'results/sqlite-adapter')
    ap.add_argument('--child', action='store_true'); ap.add_argument('--database', type=Path)
    ap.add_argument('--input',type=Path); ap.add_argument('--mode'); ap.add_argument('--policy')
    ap.add_argument('--event',type=int); ap.add_argument('--stage')
    args = ap.parse_args()
    if args.child: return child(args)
    binding = source_binding(); config = json.loads(CONFIG.read_text())
    out = args.output; out.mkdir(parents=True, exist_ok=True)
    rows = crash_campaign(config, out); costs = cost_campaign(config, out)
    if binding != source_binding(): raise AssertionError('sources changed during campaign')
    safe = [r for r in rows if r['policy'] in config['safe_policies']]
    negative = [r for r in rows if r['policy'] == config['negative_policy']]
    if not negative or not any(not r['native_ok'] for r in negative): raise AssertionError('negative control did not fail')
    groups = []
    for mode in config['journal_modes']:
        for family in config['cost_families']:
            group = {'mode':mode, 'family':family}
            for policy in config['safe_policies']:
                selected = [r for r in costs if r['mode']==mode and r['family']==family and r['policy']==policy]
                group[policy] = {k: statistics.median(r[k] for r in selected)
                                 for k in ('slots','slot_payload_bytes','write_and_read_ns','checking_ns','reopen_ns','bytes_after_checkpoint')}
            groups.append(group)
    report = {'config':config, 'environment':{'platform':platform.platform(), 'python':platform.python_version(),
              'sqlite':sqlite3.sqlite_version,'filesystem':subprocess.run(['stat','-f','-c','%T',str(out.resolve())],capture_output=True,text=True).stdout.strip() if os.name=='posix' else 'Windows'},
              'sources':binding, 'crash_cases':len(rows),'safe_cases':len(safe),
              'safe_failures':sum(not r['native_ok'] for r in safe),
              'negative_cases':len(negative),'negative_native_failures':sum(not r['native_ok'] for r in negative),
              'native_model_disagreements':sum(not r['native_model_equal'] for r in rows+costs),
              'resumed_cases':sum(r['resume_checked'] for r in rows), 'cost_runs':len(costs),
              'sql_restore_runs':sum(r['sql_restore_checked'] for r in costs), 'cost_medians':groups,
              'scope':config['scope']}
    (out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(canonical({k:v for k,v in report.items() if k not in ('cost_medians','sources','config')}))


if __name__ == '__main__': main()
