#!/usr/bin/env python3
"""Validate scientific row consistency and optionally compare a clean rerun.

No release hash manifest is required. Comparisons preserve all logical values,
identifiers, witnesses, bytes and decisions; only enumerated host timings vary.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE']='1'
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
sys.path.insert(0,str(ROOT/'src'))
from strictjson import loads
from check_scientific_invariants import check
from rocksdb_lifecycle import checked_receipt, RECEIPT
from restic_lifecycle import check as check_restic, RECEIPT as RESTIC_RECEIPT
from check_native_epochs import checked as check_epochs, RESULT as EPOCH_RECEIPT
from check_native_checkpoints import checked as check_checkpoints, RECEIPT as CHECKPOINT_RECEIPT
from publication_handoff import checked as check_handoff, RECEIPT as HANDOFF_RECEIPT
from reader_pins import checked as check_pins, RECEIPT as PIN_RECEIPT
from combined_reclamation import checked as check_combined, RECEIPT as COMBINED_RECEIPT
from restart_reclamation import checked as check_restart, RECEIPT as RESTART_RECEIPT
from spark_checkpoint_validation import (
    FIXTURE as SPARK_FIXTURE, RECEIPT as SPARK_RECEIPT, load as load_spark, summarize as check_spark,
)

HOST_FIELDS=frozenset({
    'wall_seconds','cpu_seconds','max_rss_kib','producer_cpu_seconds',
    'consumer_cpu_seconds','pair_cpu_seconds','oracle_cpu_seconds','solver_cpu_seconds',
    'recovery_ns_min','recovery_ns_median','recovery_ns_max','recovery_median_ns',
    'min_ns','median_ns','max_ns',
})
RESULT_FILES=(
 'census/graphs.jsonl','census/summary.json',
 'heldout/variants.jsonl','heldout/summary.json',
 'fallback/truth-table.jsonl','fallback/fixed-candidate.jsonl','fallback/summary.json',
 'scaling/policy-metrics.jsonl','scaling/recovery.jsonl','scaling/summary.json',
 'public/policy-metrics.jsonl','public/recovery.jsonl','public/summary.json',
 'pilots/pilot-results.jsonl','pilots/pilot-summary.json','pilots/unit-tests.json',
 'summary/summary.json',
)
def canonical(value: Any) -> Any:
    if isinstance(value,dict):
        return {k:canonical(v) for k,v in sorted(value.items()) if k not in HOST_FIELDS}
    if isinstance(value,list):return [canonical(v) for v in value]
    if isinstance(value,float) and not math.isfinite(value):raise ValueError('non-finite result')
    return value

def load_result(root:Path,name:str):
    p=root/'results'/name
    if p.suffix=='.jsonl':value=[loads(line) for line in p.read_text().splitlines() if line.strip()]
    else:value=loads(p.read_text())
    if name=='pilots/unit-tests.json':
        # Only the actual unittest elapsed-seconds field is normalized.
        value=dict(value)
        value['stderr']=re.sub(r'(Ran \d+ tests in )\d+(?:\.\d+)?(s)',r'\1<host-time>\2',value.get('stderr',''))
    return canonical(value)

def tree_files(root:Path,folder:str):
    return {p.relative_to(root/folder).as_posix():p.read_bytes() for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}

def package_hygiene(root:Path):
    errors=[]
    for directory,folders,files in os.walk(root):
        here=Path(directory)
        # A checkout's root Git metadata is not a distributed research file.
        # Nested repositories and disposable files remain errors.
        if here==root:
            folders[:]=[name for name in folders if name!='.git']
            files=[name for name in files if name!='.git']
        for name in folders:
            if name in {'.git','.hg','.svn','__pycache__'}:
                errors.append('disposable path '+(here/name).relative_to(root).as_posix())
        for name in files:
            p=here/name
            if p.suffix in {'.pyc','.zip','.aux','.bbl','.blg','.log','.out','.toc'}:
                # This one archive is an original-input corpus checked below,
                # not a second copy of the artifact or arbitrary nested output.
                if p.relative_to(root) != SPARK_FIXTURE.relative_to(ROOT):
                    errors.append('disposable or nested file '+p.relative_to(root).as_posix())
    return errors

def validate(compare:Path|None=None):
    report=check(ROOT);errors=list(report['errors'])
    errors.extend(package_hygiene(ROOT))
    try:
        native = loads(RECEIPT.read_text(encoding='utf-8'))
        checked_receipt(native)
        check_restic(loads(RESTIC_RECEIPT.read_text(encoding='utf-8')))
        check_epochs(loads(EPOCH_RECEIPT.read_text(encoding='utf-8')), native)
        check_checkpoints(loads(CHECKPOINT_RECEIPT.read_text(encoding='utf-8')))
        check_handoff(loads(HANDOFF_RECEIPT.read_text(encoding='utf-8')))
        check_pins(loads(PIN_RECEIPT.read_text(encoding='utf-8')))
        check_combined(loads(COMBINED_RECEIPT.read_text(encoding='utf-8')))
        check_restart(loads(RESTART_RECEIPT.read_text(encoding='utf-8')))
        check_spark(loads(SPARK_RECEIPT.read_text(encoding='utf-8')), load_spark())
    except (KeyError, OSError, ValueError) as error:
        errors.append(f'Native lifecycle evidence: {error}')
    comparisons=[]
    if compare is not None:
        other=check(compare)
        if other['errors']:errors+=['comparison tree invalid: '+e for e in other['errors']]
        if report['mode']!=other['mode']:
            errors.append('Cannot compare full and core-only as equivalent; validate modes separately.')
        else:
            for name in RESULT_FILES:
                equal=load_result(ROOT,name)==load_result(compare,name)
                comparisons.append({'result':name,'logical_content_equal':equal})
                if not equal:errors.append('logical result mismatch: '+name)
        for folder in ['inputs','src','scripts','tests']:
            equal=tree_files(ROOT,folder)==tree_files(compare,folder)
            comparisons.append({'directory':folder,'exact_bytes_equal':equal})
            if not equal:errors.append('exact source/input mismatch: '+folder)
    return {'schema':1,'mode':report['mode'],'scientific_checks':len(report['checks']),
            'comparisons':comparisons,'errors':errors,
            'scope':'Finite result consistency and package hygiene; not theorem verification or editorial acceptance.'}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--compare',type=Path);ap.add_argument('--run-tests',action='store_true');args=ap.parse_args()
    if args.run_tests:
        cp=subprocess.run([sys.executable,str(ROOT/'scripts/run_tests.py'),'-v'],cwd=ROOT)
        if cp.returncode:return cp.returncode
    try:report=validate(args.compare)
    except (ValueError,KeyError,TypeError,OSError) as exc:
        print('Validation failed:',exc,file=sys.stderr);return 1
    print(json.dumps(report,indent=2));return bool(report['errors'])
if __name__=='__main__':raise SystemExit(main())
